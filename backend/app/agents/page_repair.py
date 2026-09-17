from __future__ import annotations

import json
import re
from typing import Iterable

from app.models.schemas import LayoutBlock, LLMConfig, TranslatedBlock
from app.services.llm import LLMService
from app.services.protection import (
    compare_scientific_symbols,
    extract_protected_tokens,
    merge_adjacent_script_tags,
)


REPAIR_SYSTEM = """你是学术论文页级补译助手。根据本页上下文，仅为失败段落生成完整中文译文。
只输出 JSON 数组，不要 Markdown 围栏，不要解释。
每项格式：{"source_id":"...","translated_text":"..."}
要求：完整翻译对应原文；保留 Fig./Table/Eq.、单位数量、希腊字母与关系符；不要输出 [[KEEP_...]]；不要把普通数字改成 Unicode 上下标。"""

REPAIR_USER = """源语言：{source_lang}；目标语言：{target_lang}

本页块摘要（按阅读顺序，失败块已标注）：
{page_outline}

上页末段上下文：
{prev_context}

下页首段上下文：
{next_context}

请仅翻译下列失败块（必须使用相同 source_id）：
{failed_blocks}
"""


def _clip(text: str, limit: int = 220) -> str:
    text = re.sub(r'\s+', ' ', text).strip()
    return text if len(text) <= limit else text[: limit - 1] + '…'


def _is_failed(tb: TranslatedBlock) -> bool:
    text = tb.translated_text or ''
    return bool(tb.translate and text.startswith('[翻译失败:'))


def _parse_repair_json(raw: str) -> list[dict]:
    text = raw.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
    start, end = text.find('['), text.rfind(']')
    if start >= 0 and end > start:
        text = text[start: end + 1]
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError('页级补救返回值不是 JSON 数组')
    return [item for item in data if isinstance(item, dict)]


class PageRepairAgent:
    """Second-pass page-level repair for blocks that failed block-level translation."""

    def __init__(self, llm: LLMService | None = None) -> None:
        self.llm = llm or LLMService()

    async def repair_page(
        self,
        page_blocks: list[LayoutBlock],
        page_results: list[TranslatedBlock],
        *,
        source_lang: str,
        target_lang: str,
        config: LLMConfig | None = None,
        prev_blocks: Iterable[LayoutBlock] | None = None,
        next_blocks: Iterable[LayoutBlock] | None = None,
    ) -> tuple[list[TranslatedBlock], int]:
        failed_ids = {tb.source_id for tb in page_results if _is_failed(tb)}
        if not failed_ids:
            return page_results, 0

        layout_map = {b.source_id: b for b in page_blocks}
        failed_payload = []
        for tb in page_results:
            if tb.source_id not in failed_ids:
                continue
            block = layout_map.get(tb.source_id)
            if not block or not block.translate:
                continue
            failed_payload.append({
                'source_id': tb.source_id,
                'type': block.type.value,
                'failure': tb.translated_text,
                'source_text': block.text,
            })
        if not failed_payload:
            return page_results, 0

        outline_lines = []
        for block in page_blocks:
            mark = ' [FAILED]' if block.source_id in failed_ids else ''
            role = 'skip' if not block.translate else block.type.value
            outline_lines.append(f'- {block.source_id} ({role}){mark}: {_clip(block.text)}')

        prev = list(prev_blocks or [])
        next_ = list(next_blocks or [])
        prev_context = _clip(prev[-1].text, 320) if prev else '(无)'
        next_context = _clip(next_[0].text, 320) if next_ else '(无)'

        prompt = REPAIR_USER.format(
            source_lang=source_lang,
            target_lang=target_lang,
            page_outline='\n'.join(outline_lines) or '(无)',
            prev_context=prev_context,
            next_context=next_context,
            failed_blocks=json.dumps(failed_payload, ensure_ascii=False, indent=2),
        )

        cfg = config or self.llm.load_config()
        try:
            raw = await self.llm.chat(prompt, config=cfg, system_prompt=REPAIR_SYSTEM)
            items = _parse_repair_json(raw)
        except Exception:
            return page_results, 0

        repaired_map: dict[str, str] = {}
        for item in items:
            sid = str(item.get('source_id', '')).strip()
            text = str(item.get('translated_text', '')).strip()
            if not sid or sid not in failed_ids or not text or text.startswith('[翻译失败:'):
                continue
            text = re.sub(r'\[\[KEEP_[^\]]+\]\]', '', text)
            block = layout_map.get(sid)
            if not block:
                continue
            source = merge_adjacent_script_tags(block.text)
            ok, _, _ = compare_scientific_symbols(source, text)
            if not ok:
                continue
            repaired_map[sid] = text

        if not repaired_map:
            return page_results, 0

        updated: list[TranslatedBlock] = []
        count = 0
        for tb in page_results:
            if tb.source_id in repaired_map:
                updated.append(TranslatedBlock(
                    source_id=tb.source_id,
                    page=tb.page,
                    type=tb.type,
                    source_text=tb.source_text,
                    translated_text=repaired_map[tb.source_id],
                    translate=True,
                    protected_hits=extract_protected_tokens(tb.source_text),
                ))
                count += 1
            else:
                updated.append(tb)
        return updated, count
