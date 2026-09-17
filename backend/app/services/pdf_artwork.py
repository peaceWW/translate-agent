"""Visible vector bounds, respecting the PDF graphics clipping stack."""
import fitz


def visible_vector_regions(page) -> list[fitz.Rect]:
    clips = []
    visible = []
    for drawing in page.get_drawings(extended=True):
        level = drawing.get('level', 0)
        while clips and clips[-1][0] >= level:
            clips.pop()
        clip = clips[-1][1] if clips else page.rect
        if drawing['type'] == 'clip':
            clips.append((level, clip & fitz.Rect(drawing['scissor'])))
            continue
        if drawing['type'] not in {'s', 'f', 'fs'}:
            continue
        stroke = drawing.get('stroke_opacity') or 0
        fill = drawing.get('fill_opacity') or 0
        if not stroke and not fill:
            continue
        rect = fitz.Rect(drawing['rect'])
        if stroke:
            margin = max(.1, (drawing.get('width') or 0) / 2)
            rect = rect + (-margin, -margin, margin, margin)
        rect &= clip
        if not rect.is_empty:
            visible.append({'rect': rect})
    return [r for r in page.cluster_drawings(drawings=visible) if r.width > 25 and r.height > 20]
