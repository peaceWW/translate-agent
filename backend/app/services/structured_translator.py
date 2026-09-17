from __future__ import annotations

import json
import re
from typing import Optional

from app.models.schemas import LayoutBlock, LLMConfig, TranslatedBlock
from app.services.llm import LLMService
from app.services.protection import (
    compare_scientific_symbols,
    extract_protected_tokens,
    missing_figure_table_refs,
)
from app.services.semantic_builder import (
    SegmentType,
    SemanticUnit,
    TextSegment,
    assemble_segments,
    build_semantic_units,
    skeleton_for_prompt,
    split_into_segments,
    text_segments_for_prompt,
)


STRUCTURED_SYSTEM = """你是学术论文结构化翻译器。只翻译 JSON 中的 text 段落，不要翻译公式、图号、引用、数量。
只输出 JSON 对象，不要 Markdown 围栏，不要解释。
格式：
{"translations":[{"id":"T01","translation":"..."}, ...]}
要求：完整翻译对应英文；可保留英文缩写；图号若需本地化写成「图 10(a)」这类对应形式；不要输出 [[KEEP_...]]；不要把普通数字改成 Unicode 上下标。"""

STRUCTURED_USER = """源语言：{source_lang}；目标语言：{target_lang}

段落骨架（顺序必须保持）：
{skeleton}

待翻译 text 节点：
{texts}
"""

ABSORB_MARKER = "[MERGED_INTO:{primary}]"


def _parse_translations(raw: str) -> dict[str, str]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start: end + 1]
    data = json.loads(text)
    items = data.get("translations", data if isinstance(data, list) else [])
    mapping: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id", "")).strip()
        value = str(item.get("translation", item.get("text", ""))).strip()
        if sid and value:
            mapping[sid] = re.sub(r"\[\[KEEP_[^\]]+\]\]", "", value)
    return mapping


def _apply_translations(unit: SemanticUnit, mapping: dict[str, str]) -> list[TextSegment]:
    updated: list[TextSegment] = []
    for seg in unit.segments:
        if seg.type == SegmentType.TEXT and seg.source.strip():
            if seg.id not in mapping:
                raise ValueError(f"缺少 text 节点译文: {seg.id}")
            updated.append(seg.model_copy(update={"translation": mapping[seg.id]}))
        else:
            updated.append(seg)
    return updated


def _validate_assembled(source: str, translated: str) -> None:
    ok, _, _ = compare_scientific_symbols(source, translated)
    if not ok:
        # Allow extras from localization punctuation; still require no removals of greek/relations.
        # Re-check removals only.
        from app.services.protection import scientific_symbols
        removed = scientific_symbols(source) - scientific_symbols(translated)
        # Ignore script-folded digit-only differences already handled; fail on real removals.
        if removed:
            raise ValueError(f"结构化译文缺少科学符号: {dict(removed)}")
    missing = missing_figure_table_refs(source, translated)
    if missing:
        raise ValueError(f"结构化译文丢失图表引用: {', '.join(missing[:5])}")


class StructuredTranslator:
    """Translate semantic units by editing TEXT segments only."""

    def __init__(self, llm: LLMService | None = None) -> None:
        self.llm = llm or LLMService()

    async def translate_unit(
        self,
        unit: SemanticUnit,
        *,
        source_lang: str,
        target_lang: str,
        config: LLMConfig | None = None,
        layout_hint: str = "",
    ) -> str:
        texts = text_segments_for_prompt(unit)
        if not texts:
            return assemble_segments(unit.segments)

        prompt = STRUCTURED_USER.format(
            source_lang=source_lang,
            target_lang=target_lang,
            skeleton=json.dumps(skeleton_for_prompt(unit), ensure_ascii=False, indent=2),
            texts=json.dumps(texts, ensure_ascii=False, indent=2),
        )
        if layout_hint:
            prompt += f"\n排版约束：{layout_hint}"
        cfg = config or self.llm.load_config()
        raw = await self.llm.chat(prompt, config=cfg, system_prompt=STRUCTURED_SYSTEM)
        mapping = _parse_translations(raw)
        segments = _apply_translations(unit, mapping)
        assembled = assemble_segments(segments).strip()
        if not assembled:
            raise ValueError("结构化译文为空")
        _validate_assembled(unit.source_text, assembled)
        return assembled

    async def translate_block_structured(
        self,
        block: LayoutBlock,
        *,
        source_lang: str,
        target_lang: str,
        config: LLMConfig | None = None,
        layout_hint: str = "",
    ) -> TranslatedBlock:
        unit = SemanticUnit(
            unit_id=f"block_{block.source_id}",
            page=block.page,
            primary_source_id=block.source_id,
            member_source_ids=[block.source_id],
            segments=split_into_segments(block.text),
            source_text=block.text,
        )
        translated = await self.translate_unit(
            unit,
            source_lang=source_lang,
            target_lang=target_lang,
            config=config,
            layout_hint=layout_hint,
        )
        return TranslatedBlock(
            source_id=block.source_id,
            page=block.page,
            type=block.type,
            source_text=block.text,
            translated_text=translated,
            translate=True,
            protected_hits=extract_protected_tokens(block.text),
        )

    async def translate_page_units(
        self,
        page_blocks: list[LayoutBlock],
        *,
        source_lang: str,
        target_lang: str,
        config: LLMConfig | None = None,
        translate_block_fn=None,
    ) -> list[TranslatedBlock]:
        """Translate a page via semantic units; fall back per-block on failure.

        ``translate_block_fn`` should be an async callable(LayoutBlock) -> TranslatedBlock
        used when structured translation fails for a unit.
        """
        units = build_semantic_units(page_blocks)
        results_map: dict[str, TranslatedBlock] = {}

        # Non-translatable blocks first.
        for block in page_blocks:
            if not block.translate:
                results_map[block.source_id] = TranslatedBlock(
                    source_id=block.source_id,
                    page=block.page,
                    type=block.type,
                    source_text=block.text,
                    translated_text=block.text,
                    translate=False,
                    protected_hits=extract_protected_tokens(block.text),
                )

        for unit in units:
            primary = next(b for b in page_blocks if b.source_id == unit.primary_source_id)
            try:
                translated = await self.translate_unit(
                    unit,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    config=config,
                )
                results_map[unit.primary_source_id] = TranslatedBlock(
                    source_id=unit.primary_source_id,
                    page=unit.page,
                    type=primary.type,
                    source_text=unit.source_text,  # full merged sentence for QA / symbols
                    translated_text=translated,
                    translate=True,
                    protected_hits=extract_protected_tokens(unit.source_text),
                    qa={"semantic_unit": unit.unit_id, "absorb": list(unit.absorb_source_ids)},
                )
                for absorbed_id in unit.absorb_source_ids:
                    absorbed = next(b for b in page_blocks if b.source_id == absorbed_id)
                    results_map[absorbed_id] = TranslatedBlock(
                        source_id=absorbed_id,
                        page=absorbed.page,
                        type=absorbed.type,
                        source_text=absorbed.text,
                        translated_text=ABSORB_MARKER.format(primary=unit.primary_source_id),
                        translate=True,
                        protected_hits=[],
                        qa={"merged_into": unit.primary_source_id},
                    )
            except Exception:
                # Fall back to classic per-member translation (skip locked formula members).
                for member_id in unit.member_source_ids:
                    if member_id in unit.absorb_source_ids:
                        continue
                    block = next((b for b in page_blocks if b.source_id == member_id and b.translate), None)
                    if not block or member_id in results_map:
                        continue
                    if translate_block_fn is None:
                        results_map[member_id] = TranslatedBlock(
                            source_id=member_id,
                            page=block.page,
                            type=block.type,
                            source_text=block.text,
                            translated_text="[翻译失败: 结构化翻译失败]",
                            translate=True,
                        )
                    else:
                        results_map[member_id] = await translate_block_fn(block)
                for absorbed_id in unit.absorb_source_ids:
                    if absorbed_id in results_map:
                        continue
                    absorbed = next(b for b in page_blocks if b.source_id == absorbed_id)
                    if translate_block_fn is None:
                        results_map[absorbed_id] = TranslatedBlock(
                            source_id=absorbed_id, page=absorbed.page, type=absorbed.type,
                            source_text=absorbed.text,
                            translated_text="[翻译失败: 结构化翻译失败]",
                            translate=True,
                        )
                    else:
                        results_map[absorbed_id] = await translate_block_fn(absorbed)

        # Any translate block still missing → fallback
        ordered: list[TranslatedBlock] = []
        for block in page_blocks:
            if block.source_id in results_map:
                ordered.append(results_map[block.source_id])
                continue
            if not block.translate:
                ordered.append(TranslatedBlock(
                    source_id=block.source_id, page=block.page, type=block.type,
                    source_text=block.text, translated_text=block.text, translate=False,
                ))
                continue
            if translate_block_fn is not None:
                ordered.append(await translate_block_fn(block))
            else:
                ordered.append(TranslatedBlock(
                    source_id=block.source_id, page=block.page, type=block.type,
                    source_text=block.text,
                    translated_text="[翻译失败: 未生成语义单元]",
                    translate=True,
                ))
        return ordered
