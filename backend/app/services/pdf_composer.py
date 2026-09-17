from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import unicodedata
import fitz

from app.services.pdf_runtime import serialized_pdf
from app.models.schemas import LayoutBlock, TranslatedBlock
from app.services.math_layout import translated_html
from app.services.pdf_text import font_control_maps, repair_cached_controls, repair_legacy_prose_scripts
from app.services.pdf_artwork import visible_vector_regions

LAYOUT_VERSION = 5

# Overlap thresholds — relative to block area to avoid false positives from
# tiny edge overlaps that are common in PDF bbox reporting.
_MIN_OVERLAP_AREA = 5.0        # Minimum overlap area (sq pt) to consider significant
_OVERLAP_RATIO_INITIAL = 0.10  # 10% of block area — initial protected-region check
_OVERLAP_RATIO_PREPARED = 0.15 # 15% of block area — second-pass & prepared-vs-prepared check

_ABSORB_RE = re.compile(r'^\[MERGED_INTO:(.+?)\]$')


def _parse_absorb_target(text: str) -> str | None:
    match = _ABSORB_RE.match((text or '').strip())
    return match.group(1) if match else None


def _significant_overlap(a: fitz.Rect, b: fitz.Rect, ratio: float) -> bool:
    """True if the overlap between rects a and b is significant.

    Uses both an absolute floor (_MIN_OVERLAP_AREA) and a relative threshold
    (ratio × area of the smaller rect) to avoid false positives from tiny
    edge bleed common in PDF bounding boxes.
    """
    overlap = (a & b).get_area()
    if overlap < _MIN_OVERLAP_AREA:
        return False
    smaller = min(a.get_area(), b.get_area())
    if smaller < 1:
        return False
    return overlap > smaller * ratio


def _shrink_away_from_locks(rect: fitz.Rect, locks: list[fitz.Rect], original: fitz.Rect) -> bool:
    """Shrink ``rect`` to avoid significant overlap with LOCK regions.

    Returns True if the remaining box is still usable for text placement.
    Prefer pulling the expanded bottom edge back before abandoning the block.
    """
    for _ in range(4):
        hit = False
        for lock in locks:
            if not _significant_overlap(rect, lock, _OVERLAP_RATIO_INITIAL):
                continue
            hit = True
            # Lock below / overlapping bottom → pull y1 up.
            if lock.y0 + 1 < rect.y1 and lock.y0 >= original.y0 - 2:
                rect.y1 = min(rect.y1, max(original.y1, lock.y0 - 1))
            # Lock above → push y0 down.
            if lock.y1 - 1 > rect.y0 and lock.y1 <= original.y1 + 2:
                rect.y0 = max(rect.y0, min(original.y0, lock.y1 + 1))
            # Side overlap: shrink horizontally away from lock.
            inter = rect & lock
            if inter.get_area() >= _MIN_OVERLAP_AREA:
                if lock.x0 > rect.x0 and (rect.x1 - lock.x0) < (lock.x1 - rect.x0):
                    rect.x1 = min(rect.x1, lock.x0 - 1)
                elif lock.x1 < rect.x1:
                    rect.x0 = max(rect.x0, lock.x1 + 1)
        if not hit:
            break
    return rect.x1 - rect.x0 >= 24 and rect.y1 - rect.y0 >= 8


class PDFComposerService:
    """Replace text only after successful layout; keep original artwork and page geometry.

    Every page is redacted once, before adding any translations. Unplaceable blocks
    remain in the source language and are explicitly reported, never silently erased.
    """

    @serialized_pdf
    def compose_page(self, source_pdf: Path, output_pdf: Path, page_number: int,
                     layout_blocks: list[LayoutBlock], translated: list[TranslatedBlock],
                     *, report: dict) -> Path:
        """Publish a single checked page without composing unfinished pages."""
        output_pdf.parent.mkdir(parents=True, exist_ok=True)
        page_source = output_pdf.with_suffix('.source.pdf')
        try:
            with fitz.open(source_pdf) as source, fitz.open() as page_doc:
                if not 1 <= page_number <= len(source):
                    raise ValueError('页码不存在')
                page_doc.insert_pdf(source, from_page=page_number - 1, to_page=page_number - 1)
                page_doc.save(page_source)
            blocks = [b.model_copy(update={'page': 1}) for b in layout_blocks if b.page == page_number]
            results = [b.model_copy(update={'page': 1}) for b in translated if b.page == page_number]
            self.compose(page_source, output_pdf, blocks, results, report=report)
            for entry in report.get('retained_blocks', []):
                entry['page'] = page_number
            report['details'] = [f"第 {page_number} 页：{entry['reason']}" for entry in report.get('retained_blocks', [])]
            return output_pdf
        finally:
            page_source.unlink(missing_ok=True)

    @serialized_pdf
    def assemble_pages(self, source_pdf: Path, output_pdf: Path, page_files: list[Path]) -> Path:
        """Assemble exactly the checked pages already shown in the reader."""
        temporary = output_pdf.with_suffix('.building.pdf')
        try:
            with fitz.open(source_pdf) as source, fitz.open() as combined:
                if len(page_files) != len(source):
                    raise ValueError('页面未全部完成，无法导出全文')
                for index, path in enumerate(page_files):
                    with fitz.open(path) as page_doc:
                        if len(page_doc) != 1 or page_doc[0].rect != source[index].rect:
                            raise ValueError(f'第 {index + 1} 页尺寸检查失败')
                        combined.insert_pdf(page_doc)
                combined.set_metadata(source.metadata)
                combined.set_toc(source.get_toc())
                combined.save(temporary, garbage=3, deflate=True)
            temporary.replace(output_pdf)
            return output_pdf
        finally:
            temporary.unlink(missing_ok=True)

    @serialized_pdf
    def compose(self, source_pdf: Path, output_pdf: Path, layout_blocks: list[LayoutBlock],
                translated: list[TranslatedBlock], *, report: dict | None = None) -> Path:
        report = report if report is not None else {}
        report.update(version=LAYOUT_VERSION, status="passed", translated_blocks=0,
                      retained_blocks=[], details=[], page_count=0, protected_regions_checked=0,
                      pixel_warnings=[])
        translation_map = {t.source_id: t for t in translated}
        output_pdf.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_pdf.with_suffix('.building.pdf')
        try:
            with fitz.open(source_pdf) as source, fitz.open(source_pdf) as doc:
                report['page_count'] = len(source)
                for page_index, page in enumerate(doc):
                    control_maps = font_control_maps(source[page_index])
                    originals = [b for b in layout_blocks if b.page == page_index + 1]
                    raw_blocks = [b for b in source[page_index].get_text('dict')['blocks'] if b['type'] == 0]
                    # protected_artwork: true protected regions (figures, formulas, images,
                    # drawings) that must never be overwritten by translation insertion.
                    # protected: artwork + retained text blocks (used for initial overlap
                    # check and obstacle computation, but NOT for second-pass check to
                    # prevent cascading retention).
                    protected_artwork = [fitz.Rect(b.bbox.x0,b.bbox.y0,b.bbox.x1,b.bbox.y1) for b in originals if not b.translate]
                    formula_rects = [fitz.Rect(b.bbox.x0,b.bbox.y0,b.bbox.x1,b.bbox.y1)
                                     for b in originals if b.type.value == 'formula' and not b.translate]
                    image_rects = [fitz.Rect(i['bbox']) for i in source[page_index].get_image_info()]
                    protected_artwork += image_rects
                    protected_artwork += visible_vector_regions(source[page_index])
                    protected = list(protected_artwork)
                    prepared = []
                    absorb_redacts: list[fitz.Rect] = []
                    # Map primary -> absorbed layout blocks for FLOW bbox union.
                    absorb_members: dict[str, list[LayoutBlock]] = {}
                    for block in originals:
                        tb = translation_map.get(block.source_id)
                        if not tb or not tb.translate:
                            continue
                        target = _parse_absorb_target(tb.translated_text)
                        if target:
                            absorb_members.setdefault(target, []).append(block)

                    for block in originals:
                        if not block.translate:
                            continue
                        rect = fitz.Rect(block.bbox.x0,block.bbox.y0,block.bbox.x1,block.bbox.y1)
                        raw = min(raw_blocks, key=lambda b: sum(abs(a-c) for a,c in zip(b['bbox'],rect)), default=None)
                        line_rects = [fitz.Rect(line['bbox']) for line in (raw or {}).get('lines',[])] or [rect]
                        floating = [r for r in image_rects if (rect & r).get_area() > .5
                                    and abs(r.x0-rect.x0) < 2 and abs(r.y0-rect.y0) < 3
                                    and r.width < rect.width*.5 and r.height < rect.height
                                    and not any((r & line).get_area() > .5 for line in line_rects)]
                        tb = translation_map.get(block.source_id)
                        # Absorbed FLOW continuation: redact English only; primary carries text.
                        if tb and _parse_absorb_target(tb.translated_text):
                            absorb_redacts.extend(line_rects)
                            report.setdefault('absorbed_blocks', 0)
                            report['absorbed_blocks'] += 1
                            continue
                        reason = None
                        original_rect = fitz.Rect(block.bbox.x0, block.bbox.y0, block.bbox.x1, block.bbox.y1)
                        # FLOW: union bbox with absorbed members so merged sentences have room.
                        for member in absorb_members.get(block.source_id, []):
                            original_rect.y1 = max(original_rect.y1, member.bbox.y1)
                            original_rect.x0 = min(original_rect.x0, member.bbox.x0)
                            original_rect.x1 = max(original_rect.x1, member.bbox.x1)
                            member_raw = min(
                                raw_blocks,
                                key=lambda b: sum(abs(a - c) for a, c in zip(b['bbox'], (
                                    member.bbox.x0, member.bbox.y0, member.bbox.x1, member.bbox.y1))),
                                default=None,
                            )
                            for line in (member_raw or {}).get('lines', []):
                                line_rects.append(fitz.Rect(line['bbox']))
                        locks = list(formula_rects)
                        for r in protected_artwork:
                            if r not in locks:
                                locks.append(r)
                        if not tb or not tb.translate or not tb.translated_text.strip() or tb.translated_text.startswith('[翻译失败:'):
                            reason = '没有有效译文，已保留原文'
                        elif original_rect.is_empty or not page.rect.contains(original_rect):
                            reason = '文字区域越界，已保留原文'
                        if reason:
                            self._retain(report, block, reason)
                            continue
                        # Anchor/Flow: expand into free space below within column, then shrink from LOCK.
                        rect = fitz.Rect(original_rect)
                        flow_budget = 36 if absorb_members.get(block.source_id) else 20
                        limit = min(page.rect.height - 12, original_rect.y1 + flow_budget)
                        obstacles = (
                            [fitz.Rect(b.bbox.x0, b.bbox.y0, b.bbox.x1, b.bbox.y1)
                             for b in originals
                             if b.source_id != block.source_id
                             and b.source_id not in {m.source_id for m in absorb_members.get(block.source_id, [])}]
                            + locks
                        )
                        for obstacle in obstacles:
                            if min(rect.x1, obstacle.x1) > max(rect.x0, obstacle.x0) and obstacle.y0 >= original_rect.y1 - .1:
                                limit = min(limit, obstacle.y0 - 1)
                        rect.y1 = max(rect.y1, limit)
                        if floating:
                            rect.x0 = max(r.x1 for r in floating) + 5
                        if not _shrink_away_from_locks(rect, locks, original_rect):
                            self._retain(report, block, '与公式或图形保护区相交，已保留原文')
                            continue
                        raw = min(raw_blocks, key=lambda b: sum(abs(a-c) for a,c in zip(b['bbox'], (
                            block.bbox.x0, block.bbox.y0, block.bbox.x1, block.bbox.y1))), default=None)
                        spans = [s for line in (raw or {}).get('lines',[]) for s in line.get('spans',[])]
                        sizes = Counter()
                        for span in spans:
                            sizes[round(span['size'],1)] += len(span.get('text',''))
                        size = sizes.most_common(1)[0][0] if sizes else block.meta.get('font_size_hint',10)
                        size = max(7, min(size, 28))
                        bold = sum(len(s.get('text','')) for s in spans if 'bold' in s.get('font','').lower()) > sum(len(s.get('text','')) for s in spans)*.5
                        centered = block.type.value in {'title','section'} or (block.type.value != 'caption' and rect.width > page.rect.width*.65 and rect.y0 < page.rect.height*.25)
                        # Preserve semantic text, not the source PDF's arbitrary line wraps.
                        text = repair_cached_controls(tb.translated_text.strip(), spans, control_maps)
                        text = repair_legacy_prose_scripts(text, tb.source_text)
                        try:
                            markup, prose, archive = translated_html(text, size)
                        except (ValueError, RuntimeError):
                            self._retain(report,block,'行内公式无法可靠排版，已保留原文')
                            continue
                        html = '<div>' + markup + '</div>'
                        # ── Dynamic Scaling & Compression Algorithm ──
                        # When translated text is longer than the original, systematically
                        # try font-size reduction × line-height compression × scale_low
                        # combinations.  Accept the first that fits; only retain original
                        # text if even the most aggressive compression fails.
                        #
                        # Strategy priority (readability → space):
                        #   1. Original font, normal line-height (1.15)
                        #   2. Original font, compact line-height (1.10)
                        #   3. −5% font,  compact line-height (1.10)
                        #   4. −5% font,  tight   line-height (1.05)
                        #   5. −10% font, tight   line-height (1.05)
                        #   6. −10% font, minimum line-height (1.00)
                        #   7. −15% font, minimum line-height (1.00)
                        _COMPRESSION_LEVELS = [
                            # (font_scale, line_height, scale_low_override)
                            (1.00, 1.15, None),   # original — best readability
                            (1.00, 1.10, None),   # compact line-height
                            (0.95, 1.10, None),   # −5% font
                            (0.95, 1.05, None),   # −5% font + tight lines
                            (0.90, 1.05, None),   # −10% font + tight lines
                            (0.90, 1.00, None),   # −10% font + min lines
                            (0.85, 1.00, None),   # −15% font + min lines — last resort
                        ]
                        fitted = False
                        missing_glyphs = False
                        best_css = None
                        best_floor = None
                        for font_scale, lh, sl_override in _COMPRESSION_LEVELS:
                            adj_size = max(7, size * font_scale)
                            floor = sl_override if sl_override is not None else max(.60, min(1, 5.5 / adj_size))
                            css = (f'* {{margin:0;padding:0}} '
                                   f'body {{font-family:serif;font-size:{adj_size:.1f}pt;'
                                   f'line-height:{lh:.2f};color:#000}} '
                                   f'div {{text-align:{"center" if centered else "justify"};'
                                   f'font-weight:{"bold" if bold else "normal"}}}')
                            with fitz.open() as trial:
                                trial_page = trial.new_page(width=page.rect.width, height=page.rect.height)
                                spare, scale = trial_page.insert_htmlbox(
                                    rect, html, css=css, scale_low=floor, archive=archive,
                                )
                                if spare < 0:
                                    continue  # still doesn't fit — try next level
                                if not self._contains_text(prose, trial_page.get_text()):
                                    missing_glyphs = True
                                    continue
                                fitted = True
                                best_css = css
                                best_floor = floor
                                break
                        if not fitted:
                            reason = ('译文字形完整性检查未通过，已保留原文' if missing_glyphs else
                                      '译文超出原区域且无法在可读字号下容纳，已保留原文')
                            self._retain(report, block, reason)
                            continue
                        css = best_css
                        floor = best_floor
                        prepared.append((block,rect,html,css,floor,line_rects,archive,floating))
                    # If two prepared blocks overlap significantly, retain the smaller one
                    # (likely a fragment) rather than dropping both.
                    overlaps_small = set()
                    for i, item in enumerate(prepared):
                        for other in prepared[i + 1:]:
                            if _significant_overlap(item[1], other[1], _OVERLAP_RATIO_PREPARED):
                                # Keep the larger block, drop the smaller
                                if item[1].get_area() <= other[1].get_area():
                                    overlaps_small.add(item[0].source_id)
                                else:
                                    overlaps_small.add(other[0].source_id)
                    safe = []
                    for item in prepared:
                        block, rect, html, css, floor, line_rects, archive, floating = item
                        if block.source_id in overlaps_small:
                            self._retain(report, block, '与相邻译文区域重叠，已保留较小块原文')
                            continue
                        # Second pass: shrink again vs LOCK only — never vs retained prose.
                        adjusted = fitz.Rect(rect)
                        if not _shrink_away_from_locks(adjusted, formula_rects + [
                            r for r in protected_artwork if r not in formula_rects and r not in floating
                        ], fitz.Rect(block.bbox.x0, block.bbox.y0, block.bbox.x1, block.bbox.y1)):
                            self._retain(report, block, '与公式或图形保护区相交，已保留原文')
                            continue
                        safe.append((block, adjusted, html, css, floor, line_rects, archive, floating))
                    for _,_,_,_,_,line_rects,_,_ in safe:
                        for line_rect in line_rects:
                            page.add_redact_annot(line_rect,fill=False,cross_out=False)
                    for line_rect in absorb_redacts:
                        page.add_redact_annot(line_rect, fill=False, cross_out=False)
                    if safe or absorb_redacts:
                        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                                              graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                                              text=fitz.PDF_REDACT_TEXT_REMOVE)
                    for block,rect,html,css,floor,_,archive,_ in safe:
                        spare,scale = page.insert_htmlbox(rect,html,css=css,scale_low=floor,archive=archive)
                        if spare < 0:
                            raise ValueError(f'第 {block.page} 页排版回填失败，已阻止导出')
                        report['translated_blocks'] += 1
                    # Compare untouched artwork/formula crops against the source rendering.
                    # Inset comparison rect by 1pt to avoid anti-aliasing edge bleed from adjacent redaction.
                    for ri, rect in enumerate(protected):
                        inset = fitz.Rect(rect.x0 + 1, rect.y0 + 1, rect.x1 - 1, rect.y1 - 1)
                        clip = inset & page.rect
                        if clip.is_empty or clip.width < 2 or clip.height < 2:
                            report['protected_regions_checked'] += 1
                            continue
                        a = source[page_index].get_pixmap(matrix=fitz.Matrix(1,1),clip=clip,alpha=False)
                        b = page.get_pixmap(matrix=fitz.Matrix(1,1),clip=clip,alpha=False)
                        if a.samples != b.samples:
                            import numpy as np
                            aa = np.frombuffer(a.samples,dtype=np.uint8).astype(int)
                            bb = np.frombuffer(b.samples,dtype=np.uint8).astype(int)
                            diff_ratio = float(np.mean(np.abs(aa-bb)>12)) if aa.shape == bb.shape else 1.0
                            # Small regions are more sensitive to edge effects; use higher tolerance.
                            area = clip.width * clip.height
                            threshold = 0.05 if area < 2500 else 0.002
                            if aa.shape != bb.shape or diff_ratio > threshold:
                                # Soft fail: warn instead of blocking export, so user can visually verify.
                                report.setdefault('pixel_warnings', []).append(
                                    f'第 {page_index+1} 页保护区#{ri} '
                                    f'({rect.x0:.0f},{rect.y0:.0f},{rect.x1:.0f},{rect.y1:.0f}) '
                                    f'差异={diff_ratio:.4f} 阈值={threshold:.4f}'
                                )
                        report['protected_regions_checked'] += 1
                if report['retained_blocks'] or report.get('pixel_warnings'):
                    report['status'] = 'needs_review'
                if report.get('pixel_warnings'):
                    report.setdefault('details', []).extend(report['pixel_warnings'])
                doc.save(temporary,garbage=3,deflate=True)
            with fitz.open(temporary) as checked, fitz.open(source_pdf) as original:
                if len(checked) != len(original) or any(a.rect != b.rect for a,b in zip(checked,original)):
                    raise ValueError('页数或页面尺寸发生变化，已阻止导出')
            temporary.replace(output_pdf)
            return output_pdf
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _contains_text(expected: str, actual: str) -> bool:
        def normalize(s):
            return ''.join(c for c in unicodedata.normalize('NFKC',s) if not c.isspace() and c != '\u00ad')
        # PDF extraction may reorder positioned glyph runs, so compare multiplicities.
        return not (Counter(normalize(expected)) - Counter(normalize(actual)))

    @staticmethod
    def _retain(report, block, reason):
        report['retained_blocks'].append({'page':block.page,'source_id':block.source_id,'reason':reason})
        report['details'].append(f'第 {block.page} 页：{reason}')
