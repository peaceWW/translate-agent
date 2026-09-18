from __future__ import annotations

import re
from collections import Counter
from uuid import uuid4

# Shared by rendering and translation protection. Match longest units first.
UNIT = r'(?:dBm|dBc/Hz|dB/Hz|dBc|dB|[GMk]?S/s|[GMk]?b/s|[GMk]?bps|[GMk]?Hz|[fpnumµμkM]?[WVAΩFsH]|[kcmnuµμ]?m(?:[²³]|\^[23])?|ps|ns|µs|μs|ms|s|°[CF]|K|%)'
QUANTITY_PATTERN = re.compile(
    r'(?<![A-Za-z0-9_.])(?:[+−-]?\d+(?:\.\d+)?(?:[eE][+−-]?\d+)?'
    r'(?:[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+|\^[+−-]?\d+)?)'
    r'[ \t\u00a0\u202f]*' + UNIT + r'(?![A-Za-z0-9])')
SCIENTIFIC_SYMBOL_PATTERN = re.compile(
    r'[α-ωΑ-ΩµμΩÅℏℓ±∓≤≥≠≈≃≅≡≜∞∝∂∇∑∏∫√×⋅·−′″°→←↔⇒⇔∈∉⊂⊆⊃⊇∅∩∪'
    r'⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎]')

# Chinese academic prose often introduces these as punctuation / notation
# (e.g. 术语·间隔, 1×4 解复用, 65°). Require source counts to be preserved,
# but allow the translation to add more of them without failing hard checks.
TRANSLATION_EXTRA_SYMBOLS = frozenset('·°×')

# Models often rewrite ASCII / HTML digits as unicode sub/sup (v0.75 → v₀.₇₅).
# Fold them before symbol compare so they are not treated as novel math glyphs.
_SCRIPT_DIGIT_FOLD = str.maketrans({
    '⁰': '0', '¹': '1', '²': '2', '³': '3', '⁴': '4',
    '⁵': '5', '⁶': '6', '⁷': '7', '⁸': '8', '⁹': '9',
    '₀': '0', '₁': '1', '₂': '2', '₃': '3', '₄': '4',
    '₅': '5', '₆': '6', '₇': '7', '₈': '8', '₉': '9',
})

_ADJACENT_SCRIPT_TAGS = re.compile(
    r'<(sup|sub)>(.*?)</\1>\s*<\1>(.*?)</\1>',
    re.I | re.S,
)
# Numbers with optional units / scientific notation
NUMERIC_PATTERN = re.compile(
    r"(?<![A-Za-z])"
    r"(?:\d+(?:\.\d+)?(?:\s*[×xX]\s*10\^?-?\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s*(?:%|mm²|mm2|µm|um|nm|Gb/s|Gbps|Mb/s|Mbps|kHz|MHz|GHz|dB|mW|µW|uW|V|mV|µV|uV|A|mA|µA|uA|Ω|ohm|°C|K))?"
    r"(?![A-Za-z])"
)

CITATION_PATTERN = re.compile(r"\[\d+(?:\s*[,–-]\s*\d+)*\]|\(\d{4}\)")

# Fig/Table/Eq references — supports sub-figures like Fig. 4(a) and Roman numerals like Table II
FIG_TABLE_PATTERN = re.compile(
    r"\b(?:Fig\.?|Figure|Table|Eq\.?|Equation)\s*\.?\s*(?:[IVXLC]+|\d+)[A-Za-z]?(?:\([a-z]\))?(?![A-Za-z0-9])",
    re.I,
)

# Localized or spaced variants the model may emit; used only for fidelity checks.
FIG_REF_FLEX_PATTERN = re.compile(
    r'(?:Fig\.?|Figure|图|Table|表|Eq\.?|Equation|式)\s*\.?\s*'
    r'(?:[IVXLC]+|\d+)[A-Za-z]?(?:\s*[\(（][a-z][\)）])?',
    re.I,
)

FORMULA_HINT_PATTERN = re.compile(r"[=≤≥≈∈∑∫√α-ωΑ-Ω∝]|\\frac|\\sum|\\int")

# Common math/device abbreviations that do not make a block "prose".
_MATH_WORDS = {
    'noise', 'power', 'gain', 'where', 'with', 'from', 'into', 'over', 'than',
    'total', 'peak', 'input', 'output', 'ratio', 'versus', 'approx',
}

# Variable subscript patterns: V_DD, g_m, C_ox, f_clk, S_21, V_in
SUBSCRIPT_VAR_PATTERN = re.compile(
    r"\b[A-Z][a-z]?(?:_[a-zA-Z0-9±+\-]+)+\b"
)

# English calendar dates must stay intact as a phrase. Masking day/year alone
# (e.g. December [[1]], [[2019]]) makes models emit "1 月 2019 日".
MONTH_NAME = (
    r'(?:January|February|March|April|May|June|July|August|September|'
    r'October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)'
)
DATE_PATTERN = re.compile(
    rf'\b{MONTH_NAME}\.?\s+\d{{1,2}},?\s+\d{{4}}\b'
    rf'|\b\d{{1,2}}\s+{MONTH_NAME}\.?\s+\d{{4}}\b',
    re.I,
)

# Bare integers / decimals with optional unicode exponents — used for QA token
# extraction only. Do NOT use this as a KEEP mask: it breaks dates, postal codes,
# and ratios (1:4) that need free translation / reordering.
BARE_NUMBER_PATTERN = re.compile(
    r'(?<![\w.])[+−-]?\d+(?:\.\d+)?(?:[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+|\^[+−-]?\d+)?'
)


PROTECTED_BLOCK_TYPES = {
    "formula",
    "figure",
    "table",
    "header",
    "footer",
    "page_number",
    "reference",
}

TRANSLATABLE_BLOCK_TYPES = {
    "title",
    "abstract",
    "section",
    "paragraph",
    "caption",
    "table_text",
    "footnote",
}


def extract_protected_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for pattern in (QUANTITY_PATTERN, NUMERIC_PATTERN, CITATION_PATTERN, FIG_TABLE_PATTERN,
                    SUBSCRIPT_VAR_PATTERN, SCIENTIFIC_SYMBOL_PATTERN):
        tokens.extend(m.group(0) for m in pattern.finditer(text))
    # de-duplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for t in tokens:
        key = t.strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(key)
    return unique


def extract_figure_table_refs(text: str) -> list[str]:
    """Extract all Fig/Table/Eq references for cross-reference consistency checks."""
    return [m.group(0) for m in FIG_TABLE_PATTERN.finditer(text)]


def figure_ref_key(ref: str) -> str:
    """Normalize Fig. 10(a) / 图10（a） / Table II → comparable key."""
    text = ref.strip().lower().replace('（', '(').replace('）', ')')
    text = re.sub(r'\s+', '', text)
    text = re.sub(r'^(fig\.?|figure|图)', 'fig', text)
    text = re.sub(r'^(table|表)', 'table', text)
    text = re.sub(r'^(eq\.?|equation|式)', 'eq', text)
    return text


def missing_figure_table_refs(source: str, target: str) -> list[str]:
    """Return source Fig/Table/Eq refs not covered by English or localized forms in target."""
    target_keys = {figure_ref_key(m.group(0)) for m in FIG_REF_FLEX_PATTERN.finditer(target)}
    # Also accept compacted forms without the word, e.g. source Fig.10(a) → target 10(a) after 图.
    target_compact = re.sub(r'\s+', '', target.lower()).replace('（', '(').replace('）', ')')
    missing = []
    for ref in extract_figure_table_refs(source):
        key = figure_ref_key(ref)
        if key in target_keys:
            continue
        # fig10(a) / 10(a) presence as last resort for localization
        num = re.sub(r'^(fig|table|eq)', '', key)
        if num and num in target_compact and (
            '图' in target or 'fig' in target_compact or '表' in target or 'table' in target_compact
            or '式' in target or 'eq' in target_compact
        ):
            continue
        missing.append(ref)
    return missing


def looks_like_formula(text: str) -> bool:
    text = re.sub(r'</?(?:sub|sup)>', '', text).strip()
    if not text or len(text) > 400:
        return False
    if re.match(r'^(?:Fig\.?|Figure|Table)\s*\.?\s*\d+', text, re.I):
        return False
    # Lone equation numbers are display-equation fragments, not prose.
    if re.fullmatch(r'\(\d+[a-z]?\)', text):
        return True
    # Named equations can contain English descriptions and units. Their letter
    # density is no evidence that they are prose (e.g. SFDR = ... Noise power).
    lhs = r'[A-Za-zα-ωΑ-Ω][A-Za-z0-9α-ωΑ-Ω_′″/]*(?:\s*\([^\n=]{1,24}\))?'
    if re.match(r'^' + lhs + r'\s*(?:=|≜|:=|≈|∝)', text):
        return True
    compact = re.sub(r'\s+', '', text)
    if len(compact) <= 96 and re.search(r'[=≈∝]', text):
        long_words = [
            w for w in re.findall(r'[A-Za-z]{4,}', text)
            if w.lower() not in _MATH_WORDS
        ]
        if re.search(r'[=≈]', text):
            # Real equations may include a couple of unit/name words.
            if len(long_words) <= 2:
                return True
        elif not long_words:
            # Trailing ∝ alone is only formula when there is no English prose.
            return True
    if FORMULA_HINT_PATTERN.search(text):
        alpha_ratio = sum(c.isalpha() for c in text) / max(len(text), 1)
        return alpha_ratio < 0.55
    return False


def fold_script_digits(text: str) -> str:
    """Normalize unicode/HTML digits so v0.75 vs v₀.₇₅ compare equal for glyphs."""
    text = re.sub(r'</?(?:sub|sup)>', '', text)
    return text.translate(_SCRIPT_DIGIT_FOLD)


def scientific_symbols(text: str) -> Counter:
    return Counter(SCIENTIFIC_SYMBOL_PATTERN.findall(fold_script_digits(text)))


def merge_adjacent_script_tags(text: str) -> str:
    """Merge split PDF extractions like 10<sup>−</sup><sup>12</sup> → 10<sup>−12</sup>."""
    prev = None
    while prev != text:
        prev = text
        text = _ADJACENT_SCRIPT_TAGS.sub(r'<\1>\2\3</\1>', text)
    return text


def compare_scientific_symbols(source: str, target: str) -> tuple[bool, Counter, Counter]:
    """Return (ok, added, removed) with lenient extras for Chinese translation noise.

    - Non-extra symbols must match exactly (catches α lost or ≤ rewritten as ≥).
    - Extra symbols (· ° ×) may increase in the translation but must not decrease.
    """
    src = scientific_symbols(source)
    tgt = scientific_symbols(target)
    added: Counter = Counter()
    removed: Counter = Counter()
    for sym in set(src) | set(tgt):
        sc, tc = src[sym], tgt[sym]
        if sym in TRANSLATION_EXTRA_SYMBOLS:
            if tc < sc:
                removed[sym] = sc - tc
            continue
        if tc < sc:
            removed[sym] = sc - tc
        elif tc > sc:
            added[sym] = tc - sc
    return not added and not removed, added, removed


def format_symbol_diff(added: Counter, removed: Counter) -> str:
    parts: list[str] = []
    if removed:
        parts.append('缺失 ' + '、'.join(f'{s}×{n}' if n > 1 else s for s, n in sorted(removed.items())))
    if added:
        parts.append('多余 ' + '、'.join(f'{s}×{n}' if n > 1 else s for s, n in sorted(added.items())))
    return '；'.join(parts) or '符号不一致'


def mask_scientific_content(text: str) -> tuple[str, dict[str, str]]:
    """Keep exact scientific atoms out of the model's editable text.

    Intentionally does NOT mask bare calendar day/year integers by themselves:
    freezing ``1`` and ``2019`` apart from ``December`` forced ``1 月 2019 日``.
    Whole English date phrases are kept as a single atom (left in English);
    quantities with units, citations, figure refs, and symbols remain protected.
    """
    text = merge_adjacent_script_tags(text)
    patterns = [
        re.compile(r'\$\$.*?\$\$|\$[^$\n]+\$|\\\(.*?\\\)', re.S),
        re.compile(r'[A-Za-zα-ωΑ-Ω0-9]*<(?:sub|sup)>.*?</(?:sub|sup)>', re.S),
        re.compile(r'\b10\.\d{4,9}/[^\s<>]+'),
        DATE_PATTERN, QUANTITY_PATTERN, FIG_TABLE_PATTERN, CITATION_PATTERN,
        re.compile(r'\([a-z]\)'), SUBSCRIPT_VAR_PATTERN,
        # Scientific notation / unicode exponents — require a signed exponent after
        # e/E so postal fragments like ``0E9`` are not frozen as KEEP atoms.
        re.compile(
            r'(?<![\w.])[+−-]?(?:\d+\.\d+|\d+)(?:[eE][+−-]\d+|×\s*10\^?[−-]?\d+|'
            r'[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+|\^[+−-]?\d+)'
        ),
        SCIENTIFIC_SYMBOL_PATTERN,
    ]
    matches = sorted((m.start(), m.end()) for pattern in patterns for m in pattern.finditer(text))
    # Prefer the largest atom at a position; do not mask inside an existing atom.
    matches.sort(key=lambda span: (span[0], -(span[1] - span[0])))
    prefix = f'KEEP_{uuid4().hex[:12]}_'
    mapping = {}
    parts = []
    end = 0
    for start, stop in matches:
        if start < end:
            continue
        marker = f'[[{prefix}{len(mapping)}]]'
        mapping[marker] = text[start:stop]
        parts.extend((text[end:start], marker))
        end = stop
    parts.append(text[end:])
    return ''.join(parts), mapping


def restore_scientific_content(text: str, mapping: dict[str, str]) -> str:
    if any(text.count(marker) != 1 for marker in mapping):
        raise ValueError('模型遗漏或重复了公式、图注编号或科学符号')
    for marker, original in mapping.items():
        text = text.replace(marker, original)
    # Models sometimes invent extra [[KEEP_…]] tokens. Stripping them is safer
    # than failing the whole paragraph and leaving English under the layout.
    text = re.sub(r'\[\[KEEP_[^\]]+\]\]', '', text)
    return text


def should_translate(block_type: str) -> bool:
    return block_type in TRANSLATABLE_BLOCK_TYPES


def compare_protected_tokens(source: str, target: str) -> tuple[bool, list[str]]:
    """Return (ok, missing_tokens). Fig/Table refs allow localized equivalents."""
    src_figs = set(extract_figure_table_refs(source))
    tgt_set = set(extract_protected_tokens(target))
    missing: list[str] = []
    for t in extract_protected_tokens(source):
        if t in src_figs:
            continue  # checked via missing_figure_table_refs
        if t in tgt_set or t.lower() in {x.lower() for x in tgt_set}:
            continue
        missing.append(t)
    if missing:
        tgt_norm = re.sub(r'\s+', '', target)
        missing = [t for t in missing if re.sub(r'\s+', '', t) not in tgt_norm]
    for ref in missing_figure_table_refs(source, target):
        if ref not in missing:
            missing.append(ref)
    return len(missing) == 0, missing
