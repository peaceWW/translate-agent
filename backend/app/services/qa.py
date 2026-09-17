from __future__ import annotations

import re

from app.models.schemas import QASummary, TranslatedBlock
from app.services.protection import (
    compare_protected_tokens,
    compare_scientific_symbols,
    extract_protected_tokens,
    format_symbol_diff,
    merge_adjacent_script_tags,
    missing_figure_table_refs,
)


class QAService:
    """Three-layer fidelity checks: programmatic → cross-reference → (optional) semantic flags."""

    def check(self, blocks: list[TranslatedBlock]) -> QASummary:
        details: list[str] = []
        numeric_ok = True
        unit_ok = True
        formula_ok = True
        citation_ok = True
        missing_paragraphs = 0

        for b in blocks:
            if not b.translate:
                if b.type.value == "formula" and b.source_text.strip() != b.translated_text.strip():
                    formula_ok = False
                    details.append(f"公式被改写: {b.source_id}")
                continue

            if not b.translated_text.strip() or b.translated_text.startswith("[翻译失败:"):
                missing_paragraphs += 1
                details.append(f"漏译段落: {b.source_id}")
                continue
            if b.translated_text.startswith("[MERGED_INTO:"):
                continue

            source = merge_adjacent_script_tags(b.source_text)
            target = merge_adjacent_script_tags(b.translated_text)
            ok, missing = compare_protected_tokens(source, target)
            sym_ok, added, removed = compare_scientific_symbols(source, target)
            if not sym_ok:
                formula_ok = False
                details.append(f'科学符号增删或改写 [{b.source_id}]: {format_symbol_diff(added, removed)}')
            if not ok:
                for token in missing:
                    if re.search(r"\[\d+|Fig\.|Table|Eq\.", token, re.I):
                        citation_ok = False
                        details.append(f"引用/编号丢失 [{b.source_id}]: {token}")
                    elif re.search(r"(dB|mW|nm|Gb/s|%|mm)", token, re.I):
                        unit_ok = False
                        details.append(f"单位/数值丢失 [{b.source_id}]: {token}")
                    else:
                        numeric_ok = False
                        details.append(f"数值丢失 [{b.source_id}]: {token}")

            # Cross-reference consistency: Fig/Table refs must survive (allow 图 N(a))
            missing_refs = missing_figure_table_refs(b.source_text, b.translated_text)
            if missing_refs:
                citation_ok = False
                for ref in sorted(missing_refs):
                    details.append(f"图表引用丢失 [{b.source_id}]: {ref}")

        overall = all(
            [
                numeric_ok,
                unit_ok,
                formula_ok,
                citation_ok,
                missing_paragraphs == 0,
            ]
        )

        return QASummary(
            numeric_fidelity=numeric_ok,
            unit_fidelity=unit_ok,
            formula_fidelity=formula_ok,
            citation_fidelity=citation_ok,
            missing_paragraphs=missing_paragraphs,
            overall_pass=overall,
            details=details[:50],
        )

    def protected_preview(self, text: str) -> list[str]:
        return extract_protected_tokens(text)
