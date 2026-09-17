"""Recover PDF text using font evidence, without guessing scientific symbols."""
from collections import Counter
import re
import fitz


def font_control_maps(page) -> dict[str, dict[int, str]]:
    maps = {}
    doc = page.parent
    # These named Greek glyph variants occur in legacy scientific Type1 fonts.
    aliases = {'Delta1': 'Δ', 'Phi1': 'Φ', 'Omega1': 'Ω'}
    for xref, _, _, name, *_ in page.get_fonts():
        kind, encoding = doc.xref_get_key(xref, 'Encoding')
        if kind == 'xref':
            encoding = doc.xref_object(int(encoding.split()[0]))
        differences = re.search(r'/Differences\s*\[(.*?)\]', encoding, re.S)
        kind, cmap_ref = doc.xref_get_key(xref, 'ToUnicode')
        if not differences or kind != 'xref':
            continue
        cmap = (doc.xref_stream(int(cmap_ref.split()[0])) or b'').decode('ascii', errors='ignore')
        mapping = {}
        code = 0
        for token in re.findall(r'/[^\s\[\]]+|\d+', differences[1]):
            if not token.startswith('/'):
                code = int(token)
                continue
            # Repair only confirmed identity mappings to invalid control codes.
            h = f'0*{code:02X}'
            ranges = ' '.join(re.findall(r'beginbfrange(.*?)endbfrange', cmap, re.S))
            chars = ' '.join(re.findall(r'beginbfchar(.*?)endbfchar', cmap, re.S))
            identity = (re.search(fr'<{h}>\s*<{h}>\s*<{h}>', ranges, re.I)
                        or re.search(fr'<{h}>\s*<{h}>', chars, re.I))
            glyph = token[1:]
            value = aliases.get(glyph)
            if value is None:
                unicode_value = fitz.glyph_name_to_unicode(glyph)
                if unicode_value > 31 and unicode_value != 65533:
                    value = chr(unicode_value)
            if code < 32 and identity and value:
                mapping[code] = value
            code += 1
        if mapping:
            maps[name.split('+')[-1]] = mapping
    return maps


def line_text(line: dict, control_maps: dict) -> str:
    spans = line.get('spans', [])
    if not spans:
        return ''
    # A glyph's bbox bottom is below its baseline, not the baseline itself.
    main = max(spans, key=lambda s: (s.get('size', 0), len(s.get('text', ''))))
    size = main.get('size', 10)
    baselines = Counter()
    for span in spans:
        if span.get('size', 0) >= size * .9:
            baselines[round(span.get('origin', (0, 0))[1], 1)] += len(span.get('text', ''))
    baseline = baselines.most_common(1)[0][0] if baselines else 0
    parts = []
    for span in spans:
        text = span.get('text', '').translate(control_maps.get(span.get('font', '').split('+')[-1], {}))
        smaller = span.get('size', size) < size * .9
        shift = span.get('origin', (0, baseline))[1] - baseline
        sup = bool(span.get('flags', 0) & fitz.TEXT_FONT_SUPERSCRIPT)
        tag = 'sup' if sup or (smaller and shift < -size * .12) else 'sub' if smaller and shift > size * .12 else None
        parts.append(f'<{tag}>{text}</{tag}>' if tag and text.strip() else text)
    return ''.join(parts)


def repair_cached_controls(text: str, spans: list[dict], control_maps: dict) -> str:
    """Recover old translations only when source glyphs give an unambiguous value."""
    candidates = {}
    for span in spans:
        mapping = control_maps.get(span.get('font', '').split('+')[-1], {})
        for char in span.get('text', ''):
            if ord(char) < 32:
                candidates.setdefault(ord(char), set()).add(mapping.get(ord(char), char))
    return text.translate({code: next(iter(values)) for code, values in candidates.items()
                           if len(values) == 1})


def repair_legacy_prose_scripts(text: str, source: str) -> str:
    """Unwrap prose wrongly marked as superscript by the old bbox-baseline bug.

    Only activate for cached source paragraphs exhibiting that bug; leave short
    powers/indices intact and never rewrite ordinary new model output.
    """
    scripts = re.compile(r'<(sup|sub)>(.*?)</\1>', re.S)
    def is_prose(content):
        return len(re.findall(r'[A-Za-z]{2,}', content)) >= 3 or len(re.findall(r'[\u4e00-\u9fff]', content)) >= 4
    if not any(is_prose(m[2]) for m in scripts.finditer(source)):
        return text
    return scripts.sub(lambda m: m[2] if is_prose(m[2]) else m[0], text)
