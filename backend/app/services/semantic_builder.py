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
    # Merge adjacent TEXT segments
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


def _ends_incomplete(text: str) -> bool:
    plain = visible_prose(text).rstrip()
    if not plain:
        return False
    return not plain.endswith((".", "!", "?", "。", "！", "？", ":", "：", ";"))


def _starts_lowercase_continuation(text: str) -> bool:
    plain = visible_prose(text).lstrip()
    if not plain:
        return False
    ch = plain[0]
    return ch.islower() or ch in "，,;；)）"


def build_semantic_units(page_blocks: list[LayoutBlock]) -> list[SemanticUnit]:
    """Build reading-order semantic units for one page.

    Display formulas stay LOCK anchors. Adjacent FLOW prose interrupted by a
    short formula scrap can be merged into one unit so the sentence is translated whole.
    """
    ordered = sorted(page_blocks, key=lambda b: (b.bbox.y0, b.bbox.x0))
    units: list[SemanticUnit] = []
    i = 0
    unit_index = 0
    while i < len(ordered):
        block = ordered[i]
        if not block.translate:
            i += 1
            continue

        members = [block]
        absorb: list[str] = []
        j = i + 1
        # Merge across a single nearby formula scrap into the next prose block.
        if (
            j + 1 < len(ordered)
            and _ends_incomplete(block.text)
            and ordered[j].type == BlockType.FORMULA
            and not ordered[j].translate
            and ordered[j + 1].translate
            and ordered[j + 1].type in {BlockType.PARAGRAPH, BlockType.CAPTION, BlockType.ABSTRACT}
            and _same_column(block, ordered[j + 1])
            and ordered[j].bbox.y0 - block.bbox.y1 < 28
            and ordered[j + 1].bbox.y0 - ordered[j].bbox.y1 < 28
            and (
                _starts_lowercase_continuation(ordered[j + 1].text)
                or len(visible_prose(ordered[j].text)) < 64
            )
        ):
            formula = ordered[j]
            cont = ordered[j + 1]
            members = [block, cont]
            absorb = [cont.source_id]
            # Represent as: prose0 + MATH + prose1
            segs = split_into_segments(block.text, id_prefix=f"U{unit_index}A")
            math_id = f"U{unit_index}M0"
            segs.append(TextSegment(id=math_id, type=SegmentType.MATH, source=formula.text))
            segs.extend(split_into_segments(cont.text, id_prefix=f"U{unit_index}B"))
            source_text = block.text + "\n" + formula.text + "\n" + cont.text
            units.append(SemanticUnit(
                unit_id=f"unit_{block.page}_{unit_index}",
                page=block.page,
                primary_source_id=block.source_id,
                member_source_ids=[m.source_id for m in members] + [formula.source_id],
                absorb_source_ids=absorb,
                segments=segs,
                source_text=source_text,
            ))
            unit_index += 1
            i = j + 2
            continue

        segs = split_into_segments(block.text, id_prefix=f"U{unit_index}S")
        # Pure formula-like prose that slipped through should still be one MATH segment.
        if looks_like_formula(visible_prose(block.text)) and not any(s.type == SegmentType.TEXT and s.source.strip() for s in segs):
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
