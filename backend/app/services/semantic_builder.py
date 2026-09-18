from __future__ import annotations

import re
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from app.models.schemas import BlockType, LayoutBlock
from app.services.math_layout import visible_prose
from app.services.protection import (
    CITATION_PATTERN,
    DATE_PATTERN,
    FIG_TABLE_PATTERN,
    QUANTITY_PATTERN,
    SCIENTIFIC_SYMBOL_PATTERN,
    SUBSCRIPT_VAR_PATTERN,
    looks_like_formula,
)


class SegmentType(str, Enum):
    TEXT = "text"
    MATH = "math"
    FIG_REF = "fig_ref"
    CITATION = "citation"
    QUANTITY = "quantity"
    OTHER = "other"


class TextSegment(BaseModel):
    id: str
    type: SegmentType
    source: str
    translation: Optional[str] = None


class SemanticUnit(BaseModel):
    """One translatable semantic paragraph, possibly spanning multiple layout blocks."""

    unit_id: str
    page: int
    primary_source_id: str
    member_source_ids: list[str] = Field(default_factory=list)
    # Layout ids that must be redacted but not re-inserted (absorbed into primary flow).
    absorb_source_ids: list[str] = Field(default_factory=list)
    segments: list[TextSegment] = Field(default_factory=list)
    source_text: str = ""


# Inline math / symbol runs that should not be freely rewritten by the model.
_INLINE_MATH_PATTERN = re.compile(
    r'(?:'
    r'[A-Za-zα-ωΑ-Ω][A-Za-z0-9α-ωΑ-Ω_′″]*(?:<(?:sub|sup)>.*?</(?:sub|sup)>)+'
    r'|[A-Za-zα-ωΑ-Ω]?[α-ωΑ-Ω][A-Za-z0-9α-ωΑ-Ω_′″]*(?:\s*[∝≈=≤≥≠±×·⋅]\s*[^\s,;:，。；]{1,24})'
    r'|√(?:\([^)]{0,24}\)|[A-Za-zα-ωΑ-Ω_][A-Za-z0-9_]{0,12})'
    r')',
    re.S,
)

_TRAILING_ORPHAN_RADICAL = re.compile(r'√\s*$')
_TRAILING_OPEN_REL = re.compile(r'[∝≈=≤≥≠±×·⋅]\s*$')
_LEADING_RADICAND = re.compile(
    r'^\s*(?:'
    r'I\s*d\b'
    r'|I_d\b'
    r'|I<sub>d</sub>'
    r'|[A-Za-zα-ωΑ-Ω](?:_[A-Za-z0-9]{1,6}|<(?:sub|sup)>[^<]{1,12}</(?:sub|sup)>)'
    r')\s*',
    re.I,
)
_EQ_NUMBER = re.compile(r'\(\d{1,3}[a-z]?\)\s*$')
_FLOW_TYPES = {BlockType.PARAGRAPH, BlockType.CAPTION, BlockType.ABSTRACT}


def _atom_patterns() -> list[tuple[SegmentType, re.Pattern[str]]]:
    return [
        (SegmentType.FIG_REF, FIG_TABLE_PATTERN),
        (SegmentType.CITATION, CITATION_PATTERN),
        (SegmentType.QUANTITY, QUANTITY_PATTERN),
        (SegmentType.QUANTITY, DATE_PATTERN),
        (SegmentType.MATH, re.compile(r'[A-Za-zα-ωΑ-Ω0-9]*<(?:sub|sup)>.*?</(?:sub|sup)>', re.S)),
        (SegmentType.MATH, _INLINE_MATH_PATTERN),
        (SegmentType.OTHER, SUBSCRIPT_VAR_PATTERN),
        (SegmentType.MATH, SCIENTIFIC_SYMBOL_PATTERN),
    ]


def split_into_segments(text: str, *, id_prefix: str = "S") -> list[TextSegment]:
    """Split a prose string into ordered text / protected segments."""
    if not text:
        return []
    spans: list[tuple[int, int, SegmentType]] = []
    for seg_type, pattern in _atom_patterns():
        for match in pattern.finditer(text):
            spans.append((match.start(), match.end(), seg_type))
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    picked: list[tuple[int, int, SegmentType]] = []
    end = 0
    for start, stop, seg_type in spans:
        if start < end:
            continue
        picked.append((start, stop, seg_type))
        end = stop

    segments: list[TextSegment] = []
    cursor = 0
    index = 0
    for start, stop, seg_type in picked:
        if start > cursor:
            chunk = text[cursor:start]
            if chunk:
                segments.append(TextSegment(id=f"{id_prefix}{index}", type=SegmentType.TEXT, source=chunk))
                index += 1
        segments.append(TextSegment(id=f"{id_prefix}{index}", type=seg_type, source=text[start:stop]))
        index += 1
        cursor = stop
    if cursor < len(text):
        chunk = text[cursor:]
        if chunk:
            segments.append(TextSegment(id=f"{id_prefix}{index}", type=SegmentType.TEXT, source=chunk))
    merged: list[TextSegment] = []
    for seg in segments:
        if merged and merged[-1].type == SegmentType.TEXT and seg.type == SegmentType.TEXT:
            merged[-1] = TextSegment(
                id=merged[-1].id,
                type=SegmentType.TEXT,
                source=merged[-1].source + seg.source,
            )
        else:
            merged.append(seg)
    return merged


def assemble_segments(segments: list[TextSegment]) -> str:
    parts: list[str] = []
    for seg in segments:
        if seg.type == SegmentType.TEXT:
            parts.append(seg.translation if seg.translation is not None else seg.source)
        else:
            parts.append(seg.source)
    return "".join(parts)


def _same_column(a: LayoutBlock, b: LayoutBlock, *, tol: float = 36.0) -> bool:
    return abs(a.bbox.x0 - b.bbox.x0) < tol and abs(a.bbox.x1 - b.bbox.x1) < tol * 1.5


def _in_column_band(column: LayoutBlock, other: LayoutBlock, *, tol: float = 36.0) -> bool:
    """True if ``other`` sits inside ``column``'s horizontal band (scraps may be narrower)."""
    if _same_column(column, other, tol=tol):
        return True
    return (
        other.bbox.x0 >= column.bbox.x0 - tol
        and other.bbox.x1 <= column.bbox.x1 + tol
    )


def _ends_incomplete(text: str) -> bool:
    plain = visible_prose(text).rstrip()
    if not plain:
        return False
    return not plain.endswith((".", "!", "?", "。", "！", "？", ":", "：", ";"))


def _ends_orphan_math(text: str) -> bool:
    plain = visible_prose(text).rstrip()
    if not plain:
        return False
    return bool(_TRAILING_ORPHAN_RADICAL.search(plain) or _TRAILING_OPEN_REL.search(plain))


def _starts_lowercase_continuation(text: str) -> bool:
    plain = visible_prose(text).lstrip()
    if not plain:
        return False
    ch = plain[0]
    return ch.islower() or ch in "，,;；)）"


def _is_display_equation(block: LayoutBlock) -> bool:
    if block.type != BlockType.FORMULA:
        return False
    if block.meta.get('display_math'):
        return True
    if block.meta.get('inline_math'):
        return False
    plain = visible_prose(block.text)
    if _EQ_NUMBER.search(re.sub(r'\s+', ' ', plain).strip()):
        return True
    width = block.bbox.x1 - block.bbox.x0
    height = block.bbox.y1 - block.bbox.y0
    if width > 140 and len(plain) > 24:
        return True
    if height > 28 and len(plain) > 16:
        return True
    return False


def _is_inline_formula_scrap(block: LayoutBlock) -> bool:
    if block.type != BlockType.FORMULA or block.translate:
        return False
    if _is_display_equation(block):
        return False
    if block.meta.get('inline_math'):
        return True
    plain = visible_prose(block.text)
    return len(plain) < 64 and (block.bbox.y1 - block.bbox.y0) < 28


def _normalize_radicand(token: str) -> str:
    t = re.sub(r'\s+', '', visible_prose(token).strip())
    t = t.replace('I<sub>d</sub>', 'I_d')
    if re.fullmatch(r'I_?d', t, re.I):
        return 'I_d'
    return t


def _peel_orphan_radical(a_text: str, c_text: str) -> tuple[str, str, str] | None:
    """If A ends with √ and C starts with a radicand scrap, return (a_core, math, c_rest)."""
    if not _TRAILING_ORPHAN_RADICAL.search(visible_prose(a_text).rstrip()):
        return None
    match = _LEADING_RADICAND.match(c_text)
    if not match:
        plain = visible_prose(c_text)
        match = _LEADING_RADICAND.match(plain)
        if not match:
            return None
        radicand = _normalize_radicand(match.group(0))
        rest = plain[match.end():]
        a_core = _TRAILING_ORPHAN_RADICAL.sub('', a_text).rstrip()
        return a_core, f'√{radicand}', rest
    radicand = _normalize_radicand(match.group(0))
    rest = c_text[match.end():]
    a_core = _TRAILING_ORPHAN_RADICAL.sub('', a_text).rstrip()
    return a_core, f'√{radicand}', rest


def _find_same_column_merge(
    ordered: list[LayoutBlock],
    start: int,
    block: LayoutBlock,
) -> tuple[int, LayoutBlock | None] | None:
    """Find next same-column FLOW continuation, skipping other columns.

    Other-column blocks (including display equations) are skipped, never absorbed.
    Same-column display equations / figures act as hard barriers.
    """
    scrap: LayoutBlock | None = None
    for k in range(start, len(ordered)):
        cand = ordered[k]
        if not _in_column_band(block, cand):
            continue
        if not cand.translate and cand.type not in {BlockType.FORMULA}:
            return None
        if cand.type == BlockType.FORMULA and not cand.translate:
            if _is_display_equation(cand):
                return None
            if scrap is None and _is_inline_formula_scrap(cand):
                scrap = cand
                continue
            return None
        if cand.translate and cand.type in _FLOW_TYPES:
            if cand.bbox.y0 - block.bbox.y1 > 40 and scrap is None:
                return None
            if scrap is not None and cand.bbox.y0 - scrap.bbox.y1 > 40:
                return None
            if abs(cand.bbox.x0 - block.bbox.x0) > 48:
                return None
            return k, scrap
        return None
    return None


def _should_merge(block: LayoutBlock, cont: LayoutBlock, scrap: LayoutBlock | None) -> bool:
    if not _ends_incomplete(block.text) and not _ends_orphan_math(block.text):
        return False
    if _ends_orphan_math(block.text):
        return True
    if scrap is not None:
        return (
            _starts_lowercase_continuation(cont.text)
            or len(visible_prose(scrap.text)) < 64
        )
    return _starts_lowercase_continuation(cont.text) or bool(
        _LEADING_RADICAND.match(cont.text) or _LEADING_RADICAND.match(visible_prose(cont.text))
    )


def _build_merged_segments(
    unit_index: int,
    block: LayoutBlock,
    cont: LayoutBlock,
    scrap: LayoutBlock | None,
) -> tuple[list[TextSegment], str]:
    peeled = _peel_orphan_radical(block.text, cont.text)
    if peeled is not None and scrap is None:
        a_core, math_src, c_rest = peeled
        segs = split_into_segments(a_core, id_prefix=f"U{unit_index}A")
        segs.append(TextSegment(id=f"U{unit_index}M0", type=SegmentType.MATH, source=math_src))
        if c_rest.strip():
            segs.extend(split_into_segments(c_rest, id_prefix=f"U{unit_index}B"))
        return segs, block.text + "\n" + cont.text

    segs = split_into_segments(block.text, id_prefix=f"U{unit_index}A")
    if scrap is not None:
        segs.append(TextSegment(id=f"U{unit_index}M0", type=SegmentType.MATH, source=scrap.text))
    segs.extend(split_into_segments(cont.text, id_prefix=f"U{unit_index}B"))
    parts = [block.text]
    if scrap is not None:
        parts.append(scrap.text)
    parts.append(cont.text)
    return segs, "\n".join(parts)


def build_semantic_units(page_blocks: list[LayoutBlock]) -> list[SemanticUnit]:
    """Build reading-order semantic units for one page (Step B).

    - Display formulas stay LOCK anchors (never merged into prose).
    - Inline formula scraps and mid-expression splits (e.g. trailing √ / leading Id)
      are merged into one unit so the sentence is translated whole.
    - Other-column blocks are skipped and never inserted as MATH.
    """
    ordered = sorted(page_blocks, key=lambda b: (b.bbox.y0, b.bbox.x0))
    units: list[SemanticUnit] = []
    consumed: set[str] = set()
    i = 0
    unit_index = 0
    while i < len(ordered):
        block = ordered[i]
        if block.source_id in consumed or not block.translate:
            i += 1
            continue

        merge = _find_same_column_merge(ordered, i + 1, block)
        if merge is not None:
            cont_idx, scrap = merge
            cont = ordered[cont_idx]
            if cont.source_id not in consumed and _should_merge(block, cont, scrap):
                segs, source_text = _build_merged_segments(unit_index, block, cont, scrap)
                absorb = [cont.source_id]
                member_ids = [block.source_id, cont.source_id]
                if scrap is not None:
                    member_ids.append(scrap.source_id)
                    absorb.append(scrap.source_id)
                units.append(SemanticUnit(
                    unit_id=f"unit_{block.page}_{unit_index}",
                    page=block.page,
                    primary_source_id=block.source_id,
                    member_source_ids=member_ids,
                    absorb_source_ids=absorb,
                    segments=segs,
                    source_text=source_text,
                ))
                consumed.add(block.source_id)
                consumed.add(cont.source_id)
                if scrap is not None:
                    consumed.add(scrap.source_id)
                unit_index += 1
                i += 1
                continue

        segs = split_into_segments(block.text, id_prefix=f"U{unit_index}S")
        if looks_like_formula(visible_prose(block.text)) and not any(
            s.type == SegmentType.TEXT and s.source.strip() for s in segs
        ):
            segs = [TextSegment(id=f"U{unit_index}M", type=SegmentType.MATH, source=block.text)]
        units.append(SemanticUnit(
            unit_id=f"unit_{block.page}_{unit_index}",
            page=block.page,
            primary_source_id=block.source_id,
            member_source_ids=[block.source_id],
            absorb_source_ids=[],
            segments=segs,
            source_text=block.text,
        ))
        consumed.add(block.source_id)
        unit_index += 1
        i += 1
    return units


def text_segments_for_prompt(unit: SemanticUnit) -> list[dict]:
    """Payload for the LLM: only TEXT nodes need translation."""
    return [
        {"id": seg.id, "text": seg.source}
        for seg in unit.segments
        if seg.type == SegmentType.TEXT and seg.source.strip()
    ]


def skeleton_for_prompt(unit: SemanticUnit) -> list[dict]:
    """Full segment skeleton the model must preserve (non-text as id-only refs)."""
    out = []
    for seg in unit.segments:
        if seg.type == SegmentType.TEXT and seg.source.strip():
            out.append({"id": seg.id, "type": "text"})
        else:
            out.append({"id": seg.id, "type": seg.type.value, "source": seg.source})
    return out
