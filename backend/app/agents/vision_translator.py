"""Page-level vision translation: see the PDF page, translate by source_id."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from app.models.schemas import LayoutBlock, LLMConfig, TranslatedBlock
from app.services.llm import LLMService
from app.services.protection import (
    extract_protected_tokens,
    missing_figure_table_refs,
)


VISION_SYSTEM = """你是严谨的学术论文视觉翻译器。你会看到整页论文截图，以及该页需要翻译的文本区块列表。
请根据截图中的真实版式（上下标、根号、希腊字母、行内公式）理解原文，再输出对应中文译文。
只输出 JSON，不要 Markdown 围栏，不要解释。
格式：
{"translations":[{"source_id":"...","translation":"..."}, ...]}

硬性要求：
1. 必须为输入中每个 source_id 产出一条译文，不要遗漏、不要合并、不要新增 id。
2. 以截图所见为准：上下标写成 Unicode 或 HTML <sub>/<sup>；公式变量、单位、数值、引用编号、Fig./Table 编号按所见保留。
3. 不要把普通小数改成 Unicode 上下标（如勿将 0.75 写成 ₀.₇₅）。
4. text_hint 仅供参考，可能因 PDF 提取丢失上下标；发现冲突时以截图为准。
5. 中文译文紧凑专业，视觉长度接近原段落，勿展开解释。
6. 专业缩写（ADC、PLL、CMOS 等）保留英文。
"""

VISION_USER = """源语言：{source_lang}；目标语言：{target_lang}
页码：{page}

待翻译区块（按阅读顺序；bbox 为 PDF 点坐标 [x0,y0,x1,y1]）：
{blocks_json}
"""


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
        sid = str(item.get("source_id", item.get("id", ""))).strip()
        value = str(item.get("translation", item.get("text", ""))).strip()
        if sid and value:
            mapping[sid] = re.sub(r"\[\[KEEP_[^\]]+\]\]", "", value)
    return mapping


def _soft_validate(source: str, translated: str) -> Optional[str]:
    """Return an error message for hard failures only (soft on symbol count)."""
    if not translated.strip():
        return "译文为空"
    missing = missing_figure_table_refs(source, translated)
    if missing:
        return f"丢失图表引用: {', '.join(missing[:5])}"
    # Critical quantities / citations — keep a light check without KEEP masks.
    for token in extract_protected_tokens(source):
        if re.search(r"\[\d+", token) and token not in translated and token.lower() not in translated.lower():
            # Allow spaced variants inside Chinese prose.
            compact = re.sub(r"\s+", "", token)
            if compact not in re.sub(r"\s+", "", translated):
                return f"丢失引用编号: {token}"
        if re.search(r"(?:dBm|dB|Gb/s|GHz|MHz|%)\b", token, re.I):
            # Quantity must appear in some form; strip spaces for compare.
            compact = re.sub(r"\s+", "", token)
            if compact not in re.sub(r"\s+", "", translated):
                # Soft: numeric part alone may survive with localized unit — skip hard fail.
                num = re.match(r"[+−-]?\d+(?:\.\d+)?", token)
                if num and num.group(0) not in translated:
                    return f"丢失关键数值单位: {token}"
    return None


def _layout_budget_hint(block: LayoutBlock) -> str:
    bbox = block.bbox
    width_pt = bbox.x1 - bbox.x0
    height_pt = bbox.y1 - bbox.y0
    font_size = float(block.meta.get("font_size_hint", 10) or 10)
    if width_pt < 10 or height_pt < 5 or font_size < 5:
        return ""
    line_height = font_size * 1.15
    approx_lines = max(1, round(height_pt / line_height))
    chars_per_line = int(width_pt / (font_size * 0.7))
    budget = int(approx_lines * chars_per_line * 0.85)
    return f"约{budget}字"


class VisionTranslationAgent:
    """Translate a page by looking at the rendered page image."""

    def __init__(self, llm: LLMService | None = None) -> None:
        self.llm = llm or LLMService()

    def _block_payload(self, block: LayoutBlock) -> dict:
        hint = (block.text or "")[:800]
        payload = {
            "source_id": block.source_id,
            "type": block.type.value if hasattr(block.type, "value") else str(block.type),
            "bbox": [round(block.bbox.x0, 1), round(block.bbox.y0, 1),
                     round(block.bbox.x1, 1), round(block.bbox.y1, 1)],
            "text_hint": hint,
        }
        budget = _layout_budget_hint(block)
        if budget:
            payload["length_hint"] = budget
        return payload

    async def translate_page(
        self,
        page_blocks: list[LayoutBlock],
        image_path: Path,
        *,
        source_lang: str,
        target_lang: str,
        config: LLMConfig | None = None,
    ) -> list[TranslatedBlock]:
        """Return one TranslatedBlock per input block; failures marked for text fallback."""
        results: list[TranslatedBlock] = []
        translateable = [b for b in page_blocks if b.translate and (b.text or "").strip()]
        for block in page_blocks:
            if not block.translate:
                results.append(TranslatedBlock(
                    source_id=block.source_id,
                    page=block.page,
                    type=block.type,
                    source_text=block.text,
                    translated_text=block.text,
                    translate=False,
                    protected_hits=extract_protected_tokens(block.text),
                ))
            elif not (block.text or "").strip():
                results.append(TranslatedBlock(
                    source_id=block.source_id,
                    page=block.page,
                    type=block.type,
                    source_text=block.text,
                    translated_text=block.text,
                    translate=True,
                    protected_hits=[],
                ))

        if not translateable:
            return results

        page = translateable[0].page
        payload = [self._block_payload(b) for b in translateable]
        prompt = VISION_USER.format(
            source_lang=source_lang,
            target_lang=target_lang,
            page=page,
            blocks_json=json.dumps(payload, ensure_ascii=False, indent=2),
        )
        base = config or self.llm.load_config()
        cfg = base.effective_vision_config()
        # Page JSON can be large; bump tokens if the shared default is tight.
        if cfg.max_tokens < 8192:
            cfg = cfg.model_copy(update={"max_tokens": 8192})

        try:
            raw = await self.llm.chat_with_images(
                prompt,
                [image_path],
                system_prompt=VISION_SYSTEM,
                config=cfg,
            )
            mapping = _parse_translations(raw)
        except Exception as exc:  # noqa: BLE001
            for block in translateable:
                results.append(TranslatedBlock(
                    source_id=block.source_id,
                    page=block.page,
                    type=block.type,
                    source_text=block.text,
                    translated_text=f"[翻译失败: 视觉翻译失败: {exc}]",
                    translate=True,
                    protected_hits=extract_protected_tokens(block.text),
                    qa={"vision": False, "error": str(exc)},
                ))
            return results

        for block in translateable:
            translated = mapping.get(block.source_id, "").strip()
            if not translated:
                results.append(TranslatedBlock(
                    source_id=block.source_id,
                    page=block.page,
                    type=block.type,
                    source_text=block.text,
                    translated_text="[翻译失败: 视觉模型未返回该区块]",
                    translate=True,
                    protected_hits=extract_protected_tokens(block.text),
                    qa={"vision": False, "error": "missing_source_id"},
                ))
                continue
            err = _soft_validate(block.text, translated)
            if err:
                results.append(TranslatedBlock(
                    source_id=block.source_id,
                    page=block.page,
                    type=block.type,
                    source_text=block.text,
                    translated_text=f"[翻译失败: {err}]",
                    translate=True,
                    protected_hits=extract_protected_tokens(block.text),
                    qa={"vision": False, "error": err},
                ))
                continue
            results.append(TranslatedBlock(
                source_id=block.source_id,
                page=block.page,
                type=block.type,
                source_text=block.text,
                translated_text=translated,
                translate=True,
                protected_hits=extract_protected_tokens(block.text),
                qa={"vision": True},
            ))

        # Preserve original page block order for composer.
        order = {b.source_id: i for i, b in enumerate(page_blocks)}
        results.sort(key=lambda tb: order.get(tb.source_id, 10_000))
        return results
