from __future__ import annotations

import re
from pathlib import Path
from typing import Optional
from uuid import uuid4

import fitz  # PyMuPDF

from app.services.pdf_runtime import serialized_pdf
from app.models.schemas import BBox, BlockType, LayoutBlock
from app.services.protection import looks_like_formula, should_translate, SCIENTIFIC_SYMBOL_PATTERN
from app.services.pdf_text import font_control_maps, line_text
from app.services.math_layout import visible_prose
from app.services.pdf_artwork import visible_vector_regions


SECTION_RE = re.compile(r"^(?:\d+(?:\.\d+)*\.?\s+|[IVXLC]+\.\s+)[A-Z].+")
CAPTION_RE = re.compile(r"^(?:Fig\.?|Figure|Table)\s*\.?\s*\d+", re.I)


class PDFParserService:
    """Parse PDF into a layout tree of blocks with bbox and translate flags."""

    @serialized_pdf
    def parse(self, pdf_path: Path, asset_dir: Optional[Path] = None) -> list[LayoutBlock]:
        fitz.TOOLS.set_small_glyph_heights(True)
        doc = fitz.open(pdf_path)
        blocks: list[LayoutBlock] = []
        asset_dir = asset_dir or pdf_path.parent / f"{pdf_path.stem}_assets"
        asset_dir.mkdir(parents=True, exist_ok=True)

        for page_index in range(len(doc)):
            page = doc[page_index]
            page_dict = page.get_text("dict")
            control_maps = font_control_maps(page)
            page_height = page.rect.height
            image_rects = [fitz.Rect(info["bbox"]) for info in page.get_image_info()]
            vector_rects = visible_vector_regions(page)

            # Extract images first
            for img_i, img in enumerate(page.get_images(full=True)):
                xref = img[0]
                try:
                    pix = fitz.Pixmap(doc, xref)
                    if pix.n >= 5:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    img_name = f"p{page_index + 1}_img{img_i + 1}.png"
                    img_path = asset_dir / img_name
                    pix.save(str(img_path))
                    # Approximate bbox via image rects if available
                    rects = page.get_image_rects(xref)
                    if rects:
                        r = rects[0]
                        blocks.append(
                            LayoutBlock(
                                source_id=f"fig_{page_index + 1}_{img_i + 1}",
                                page=page_index + 1,
                                type=BlockType.FIGURE,
                                bbox=BBox(x0=r.x0, y0=r.y0, x1=r.x1, y1=r.y1),
                                text="",
                                translate=False,
                                protected=True,
                                image_path=str(img_path),
                            )
                        )
                except Exception:  # noqa: BLE001
                    continue

            for bi, raw in enumerate(page_dict.get("blocks", [])):
                if raw.get("type") != 0:
                    continue  # skip image blocks here (handled above)
                lines = []
                has_subscript = False
                has_superscript = False
                for line in raw.get("lines", []):
                    span_text = line_text(line, control_maps)
                    has_superscript |= '<sup>' in span_text
                    has_subscript |= '<sub>' in span_text
                    if span_text.strip():
                        lines.append(span_text)
                text = "\n".join(lines).strip()
                if not text:
                    continue

                bbox = raw.get("bbox", [0, 0, 0, 0])
                plain_text = visible_prose(text)
                block_type = self._classify(plain_text, bbox, page_height)
                translate = should_translate(block_type.value) and not looks_like_formula(plain_text)
                if block_type != BlockType.CAPTION and looks_like_formula(plain_text):
                    block_type = BlockType.FORMULA
                    translate = False

                spans = [s for line in raw.get("lines", []) for s in line.get("spans", [])]
                rect = fitz.Rect(bbox)
                # Labels inside diagrams must stay attached to the original artwork.
                # But guard against oversized drawing clusters that accidentally
                # encompass text blocks far from the actual artwork.
                inside_art = any(
                    r.contains(rect) and r.get_area() < rect.get_area() * 8
                    for r in image_rects + vector_rects
                )
                math_chars = sum(len(s.get("text", "")) for s in spans if any(k in s.get("font", "").lower() for k in ("symbol", "cmmi", "cmsy", "math")))
                if (inside_art and block_type != BlockType.CAPTION) or not any(c.isalpha() for c in text) or (block_type != BlockType.CAPTION and math_chars > 0 and len(text) < 80 and math_chars / max(len(text), 1) > .30):
                    translate = False
                    block_type = BlockType.FIGURE if inside_art else BlockType.FORMULA
                font_size = self._avg_font_size(raw)
                if page_index == 0 and bbox[1] < page_height * .25 and font_size > 17:
                    block_type = BlockType.TITLE
                blocks.append(
                    LayoutBlock(
                        source_id=f"b_{page_index + 1}_{bi + 1}_{uuid4().hex[:6]}",
                        page=page_index + 1,
                        type=block_type,
                        bbox=BBox(x0=bbox[0], y0=bbox[1], x1=bbox[2], y1=bbox[3]),
                        text=text,
                        translate=translate,
                        protected=not translate,
                        meta={
                            "font_size_hint": font_size,
                            "bold": sum(len(s.get("text", "")) for s in spans if "bold" in s.get("font", "").lower()) > len(text) * .5,
                            "centered": block_type in {BlockType.TITLE, BlockType.SECTION} or rect.width > page.rect.width * .7,
                            "line_rects": [line["bbox"] for line in raw.get("lines", [])],
                            "page_width": page.rect.width,
                            "page_height": page.rect.height,
                            "has_subscript": has_subscript,
                            "has_superscript": has_superscript,
                        },
                    )
                )

        doc.close()
        self._protect_formula_fragments(blocks)
        self._lock_display_equations(blocks)
        # Reading order: page, then top-to-bottom, left-to-right
        blocks.sort(key=lambda b: (b.page, b.bbox.y0, b.bbox.x0))
        return blocks

    @staticmethod
    def _mark_formula(block: LayoutBlock) -> None:
        block.type = BlockType.FORMULA
        block.translate = False
        block.protected = True

    @classmethod
    def _lock_display_equations(cls, blocks: list[LayoutBlock]) -> None:
        """Force display-equation pieces off the translation path.

        PDF often splits ``kT/CIN = …`` and ``(6)`` into paragraphs that still
        carry translate=True; composing them then collides with formula locks.
        """
        for block in blocks:
            if block.type == BlockType.CAPTION:
                continue
            plain = visible_prose(block.text)
            if looks_like_formula(plain) or re.fullmatch(r'\(\d+[a-z]?\)', plain.strip()):
                cls._mark_formula(block)

        # Equation numbers / short scraps near an existing formula → lock.
        changed = True
        while changed:
            changed = False
            for block in blocks:
                if not block.translate or block.type == BlockType.CAPTION:
                    continue
                plain = visible_prose(block.text)
                r = block.bbox
                near = any(
                    other.page == block.page and other.type == BlockType.FORMULA and not other.translate
                    and min(r.y1, other.bbox.y1) - max(r.y0, other.bbox.y0) > -4
                    and max(r.x0, other.bbox.x0) - min(r.x1, other.bbox.x1) < 48
                    for other in blocks
                )
                if not near:
                    continue
                if (
                    re.fullmatch(r'\(\d+[a-z]?\)', plain.strip())
                    or looks_like_formula(plain)
                    or (len(plain) < 48 and SCIENTIFIC_SYMBOL_PATTERN.search(plain) and not re.search(r'[A-Za-z]{5,}', plain))
                ):
                    cls._mark_formula(block)
                    changed = True

    @staticmethod
    def _protect_formula_fragments(blocks: list[LayoutBlock]) -> None:
        # PDF equations are often split into numerator, denominator and number.
        # Only compact, variable-only fragments may join an existing equation;
        # prose such as "where I_pd is ..." must remain translatable.
        candidates = []
        for block in blocks:
            plain = visible_prose(block.text)
            words = re.findall(r'[A-Za-z]+', plain)
            unit_label = bool(re.fullmatch(r'[A-Za-z ]{1,32}\((?:dBm|dB|W|V|A|Hz)\)[ .]*', plain.strip()))
            numbered_tail = bool(re.match(r'^[\d(\[+−-]', plain) and re.search(r'\(\d+[a-z]?\)\s*$', plain))
            # Greek/math scraps like "ω" have no Latin words, so the old heuristic
            # skipped them and left translate=True → composer retained English on top.
            math_scrap = (
                len(plain) < 24
                and not re.search(r'[A-Za-z]{4,}', plain)
                and bool(SCIENTIFIC_SYMBOL_PATTERN.search(plain) or re.search(r'[=+−×÷/√∝]', plain))
            )
            wordy_fragment = bool(words) and (
                unit_label or numbered_tail
                or all(len(w) <= 2 or w.isupper() and len(w) <= 5
                       or re.fullmatch(r'[a-z][A-Z]{1,5}', w)
                       or re.fullmatch(r'[A-Za-z]{0,3}(?:pd|total|max|min|sin|cos|log|exp)', w)
                       for w in words)
            )
            if (block.translate and block.type != BlockType.CAPTION
                    and block.bbox.y1 - block.bbox.y0 < 40 and len(plain) < 100
                    and (math_scrap or wordy_fragment or looks_like_formula(plain))):
                candidates.append(block)
        changed = True
        while changed:
            changed = False
            for block in candidates:
                if not block.translate:
                    continue
                r = block.bbox
                near_formula = any(
                    other.page == block.page and other.type == BlockType.FORMULA
                    and min(r.y1, other.bbox.y1) - max(r.y0, other.bbox.y0) > 0
                    and max(r.x0, other.bbox.x0) - min(r.x1, other.bbox.x1) < 40
                    for other in blocks
                )
                if near_formula or re.search(r'[=+−∝]', visible_prose(block.text)) or (
                        len(visible_prose(block.text)) < 8 and SCIENTIFIC_SYMBOL_PATTERN.search(block.text)):
                    block.type = BlockType.FORMULA
                    block.translate = False
                    block.protected = True
                    changed = True

    @serialized_pdf
    def page_count(self, pdf_path: Path) -> int:
        doc = fitz.open(pdf_path)
        n = len(doc)
        doc.close()
        return n

    def _avg_font_size(self, raw_block: dict) -> float:
        sizes: list[float] = []
        for line in raw_block.get("lines", []):
            for span in line.get("spans", []):
                if "size" in span:
                    sizes.append(float(span["size"]))
        return sum(sizes) / len(sizes) if sizes else 0.0

    def _classify(self, text: str, bbox: list[float], page_height: float) -> BlockType:
        y0, y1 = bbox[1], bbox[3]
        # Captions can be at the top/bottom of a page; do not discard them as
        # running headers or footers merely because of their position.
        if CAPTION_RE.match(text.strip()):
            return BlockType.CAPTION
        if y1 < page_height * 0.06:
            return BlockType.HEADER
        if y0 > page_height * 0.94:
            if text.strip().isdigit():
                return BlockType.PAGE_NUMBER
            return BlockType.FOOTER
        if CAPTION_RE.match(text.strip()):
            return BlockType.CAPTION
        if SECTION_RE.match(text.strip()) and len(text) < 120:
            return BlockType.SECTION
        if text.lower().startswith("abstract"):
            return BlockType.ABSTRACT
        if text.lower().startswith("references") or text.lower().startswith("bibliography"):
            return BlockType.REFERENCE
        if looks_like_formula(text):
            return BlockType.FORMULA
        # Title heuristic: near top, short, not a sentence/caption
        if (
            y0 < page_height * 0.12
            and len(text) < 180
            and "\n" not in text
            and not text.rstrip().endswith((".", ";", ":"))
            and not any(k in text for k in ("Fig.", "Table", "see ", "See "))
        ):
            return BlockType.TITLE
        return BlockType.PARAGRAPH
