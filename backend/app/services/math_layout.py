from __future__ import annotations
from functools import lru_cache
from html import escape
from io import BytesIO
from threading import Lock
import re
import os
import fitz
from app.services.protection import QUANTITY_PATTERN

_MATH = re.compile(r'\$\$(.+?)\$\$|\$(.+?)\$|\\\((.+?)\\\)', re.S)
_LOCK = Lock()

# Placeholder markers for sub/sup tags that must survive html.escape()
_SUB_OPEN = '\x01sub\x02'
_SUB_CLOSE = '\x01/sub\x02'
_SUP_OPEN = '\x01sup\x02'
_SUP_CLOSE = '\x01/sup\x02'


def visible_prose(text: str) -> str:
    """Only supported formatting tags disappear when HTML is rendered."""
    return re.sub(r'</?(?:sub|sup)>', '', text)

@lru_cache(maxsize=256)
def _formula_svg(expression: str, size: float):
    from app.core.config import get_settings
    cache = get_settings().data_dir / "font-cache"
    cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(cache.resolve()))
    from matplotlib.mathtext import math_to_image
    from matplotlib.font_manager import FontProperties
    stream = BytesIO()
    with _LOCK:
        math_to_image('$' + expression.strip() + '$', stream,
                      prop=FontProperties(size=size, math_fontfamily='stix'), format='svg')
    data = stream.getvalue()
    with fitz.open(stream=data, filetype='svg') as doc:
        width, height = doc[0].rect.width, doc[0].rect.height
    return data, width, height


def translated_html(text: str, size: float):
    """Escape prose; shape inline math locally (no model, network, or executable TeX)."""
    archive = fitz.Archive()
    prose = []
    html = []
    offset = 0
    for index, match in enumerate(_MATH.finditer(text)):
        before = text[offset:match.start()]
        prose.append(before)
        html.append(_prose(before))
        expression = next(x for x in match.groups() if x is not None)
        data, width, height = _formula_svg(expression, size)
        name = f'formula-{index}.svg'
        archive.add(data, name)
        html.append(f'<img src="{name}" style="width:{width}pt;height:{height}pt;vertical-align:middle"/>')
        offset = match.end()
    rest = text[offset:]
    prose.append(rest)
    html.append(_prose(rest))
    return ''.join(html), visible_prose(''.join(prose)), archive


def _prose(text):
    superscripts = str.maketrans('⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾', '0123456789+−=()')
    subscripts = str.maketrans('₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎', '0123456789+-=()')

    # Protect <sub>...</sub> and <sup>...</sup> from html.escape()
    text = text.replace('<sub>', _SUB_OPEN).replace('</sub>', _SUB_CLOSE)
    text = text.replace('<sup>', _SUP_OPEN).replace('</sup>', _SUP_CLOSE)

    text = escape(text).replace('\n\n', '<br/>').replace('\n', ' ')

    # Restore sub/sup tags
    text = text.replace(_SUB_OPEN, '<sub>').replace(_SUB_CLOSE, '</sub>')
    text = text.replace(_SUP_OPEN, '<sup>').replace(_SUP_CLOSE, '</sup>')

    # Unicode subscript characters → <sub>
    text = re.sub('[₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎]+', lambda m: '<sub>' + m.group().translate(subscripts) + '</sub>', text)
    # Unicode superscript characters → <sup>
    text = re.sub('[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾]+', lambda m: '<sup>' + m.group().translate(superscripts) + '</sup>', text)

    # Justification stretches ordinary spaces. A quantity is a single inline
    # unit with fixed nonbreaking spacing, never a number on one line and its
    # unit on another. Preserve whether the source used a separator at all.
    text = QUANTITY_PATTERN.sub(
        lambda m: '<span style="white-space:nowrap">' +
        re.sub(r'[ \t\u00a0\u202f]+', '&#160;', m.group()) + '</span>', text)

    return text
