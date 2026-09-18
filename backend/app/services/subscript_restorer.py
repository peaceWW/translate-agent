"""SubscriptRestorer — 上下标与专业符号精准提取与还原.

三阶段流水线:
  Stage 1: 正则粗筛 → 候选列表 + 置信度
  Stage 2: 领域白名单(Glossary)纠偏 → 确认/排除/待LLM
  Stage 3: LLM 上下文精修 → 最终LaTeX还原
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Data Structures
# ─────────────────────────────────────────────

class MatchSource(str, Enum):
    REGEX = "regex"
    GLOSSARY = "glossary"
    LLM = "llm"


@dataclass
class Candidate:
    """A suspected variable / subscript / superscript candidate."""
    original: str
    start: int
    end: int
    confidence: float
    source: MatchSource
    rule_name: str
    proposed_latex: str = ""
    context: str = ""


@dataclass
class ReplaceLog:
    """Record of a single replacement made during restoration."""
    original: str
    replacement: str
    position: int
    source: MatchSource
    rule_name: str
    confidence: float


@dataclass
class RestoredResult:
    """Result of subscript restoration on a text string."""
    text: str
    log: list[ReplaceLog] = field(default_factory=list)


# ─────────────────────────────────────────────
# Stage 1: Regex Coarse Filter
# ─────────────────────────────────────────────

# Stop words — common English words that regex patterns may falsely match.
STOP_WORDS: frozenset[str] = frozenset({
    "in", "on", "at", "an", "is", "it", "of", "or", "as", "be",
    "by", "do", "go", "he", "me", "no", "so", "up", "us", "we",
    "if", "to", "am", "ok", "not", "and", "but", "for", "the",
    "can", "has", "had", "was", "are", "been", "from", "that",
    "this", "with", "will", "would", "could", "should",
    "all", "any", "its", "let", "may", "nor", "own", "per",
    "via", "yet", "add", "sub", "set", "get", "put", "run",
    "see", "use", "way", "day", "new", "old", "big", "top",
})

# Greek letters (Unicode) commonly used as variable prefixes.
_GREEK_CHARS = "αβγδεζηθικλμνξπρστυφχψωΔΩΣ∏∫"
_GREEK_CLASS = f"[{_GREEK_CHARS}]"

# ── Compiled patterns: (rule_name, pattern, confidence) ──
_PATTERNS: list[tuple[str, re.Pattern[str], float]] = [
    # A. Underscript residue: V_DD, g_m, C_ox, f_clk
    (
        "underscore_subscript",
        re.compile(r"\b([a-zA-Z]{1,3})_([a-zA-Z0-9]{1,4})\b"),
        0.95,
    ),
    # B. Single letter + numeric subscript: v1, I3, n2
    (
        "single_letter_numeric_sub",
        re.compile(r"(?<![a-zA-Z])([a-zA-Z])(\d+)(?![a-zA-Z])"),
        0.90,
    ),
    # C. Greek prefix + subscript: Δv1, αmax, ωc
    #    Uses explicit Unicode class built at import time for portability.
    (
        "greek_prefix_sub",
        re.compile(
            f"(?<![a-zA-Z])({_GREEK_CLASS})([a-zA-Z0-9]{{1,5}})(?![a-zA-Z])"
        ),
        0.88,
    ),
    # D. Capital variable + capital subscript: VDD, VGS, VDS, VSS
    (
        "capital_var_capital_sub",
        re.compile(r"(?<![a-zA-Z])([A-Z])([A-Z]{2,3})(?![a-zA-Z])"),
        0.85,
    ),
    # E. Two-capital variable + subscript: RCIN, CIN
    (
        "two_capital_var_sub",
        re.compile(r"(?<![a-zA-Z])([A-Z]{2})([A-Z]{1,3})(?![a-zA-Z])"),
        0.70,
    ),
    # F. Single letter + lowercase subscript: gm, Id, fn, fc
    (
        "single_letter_lowercase_sub",
        re.compile(r"(?<![a-zA-Z])([a-zA-Z])([a-z]{1,4})(?![a-zA-Z])"),
        0.65,
    ),
    # G. Mixed case + number subscript: f3dB, R50Ω, V2th
    (
        "mixed_case_numeric_sub",
        re.compile(r"(?<![a-zA-Z])([a-zA-Z])(\d+[a-zA-Z]{1,3})(?![a-zA-Z])"),
        0.75,
    ),
    # H. Two capital letters (variable + 1-char subscript): RL, RS, IL
    #    Low confidence — many false positives (IN, IT, IP, etc.)
    #    but glossary exclude list will filter those out.
    (
        "two_capital_letters",
        re.compile(r"(?<![a-zA-Z])([A-Z])([A-Z])(?![a-zA-Z])"),
        0.55,
    ),
    # I. Numeric superscript (context-dependent): x2, n3
    (
        "numeric_superscript",
        re.compile(
            r"(?<![a-zA-Z_])([a-zA-Z])([²³]|2|3)(?![a-zA-Z0-9])"
        ),
        0.50,
    ),
]


def coarse_filter(text: str) -> list[Candidate]:
    """Stage 1: Apply all regex patterns and collect candidates.

    Overlapping matches are deduplicated — highest confidence wins,
    longest match wins among equal confidence.
    """
    candidates: list[Candidate] = []

    for rule_name, pattern, confidence in _PATTERNS:
        for m in pattern.finditer(text):
            matched = m.group(0)
            # Skip stop words
            if matched.lower() in STOP_WORDS:
                continue
            # Skip very short matches that are likely noise
            if len(matched) < 2:
                continue
            ctx_start = max(0, m.start() - 30)
            ctx_end = min(len(text), m.end() + 30)
            candidates.append(Candidate(
                original=matched,
                start=m.start(),
                end=m.end(),
                confidence=confidence,
                source=MatchSource.REGEX,
                rule_name=rule_name,
                proposed_latex="",
                context=text[ctx_start:ctx_end],
            ))

    return _deduplicate_overlapping(candidates)


def _deduplicate_overlapping(candidates: list[Candidate]) -> list[Candidate]:
    """Remove overlapping candidates: keep highest confidence, then longest."""
    if not candidates:
        return []

    # Sort by (start, -confidence, -length) so first occurrence at each
    # position is the best one.
    candidates.sort(key=lambda c: (c.start, -c.confidence, -(c.end - c.start)))

    result: list[Candidate] = []
    last_end = -1

    for c in candidates:
        if c.start >= last_end:
            # No overlap with previous kept candidate
            result.append(c)
            last_end = c.end
        elif c.confidence > result[-1].confidence:
            # Overlaps but higher confidence — replace previous
            result[-1] = c
            last_end = c.end
        # else: overlaps with lower/equal confidence — discard

    return result


# ─────────────────────────────────────────────
# Stage 2: Glossary (Domain Whitelist)
# ─────────────────────────────────────────────

@dataclass
class GlossaryEntry:
    flat: str
    latex: Optional[str]
    desc: str
    type: str  # subscript / superscript / exclude


class Glossary:
    """Domain-specific whitelist for subscript/superscript disambiguation."""

    def __init__(self, domain: str = "photonic_ic") -> None:
        self.entries: dict[str, GlossaryEntry] = {}
        self._load_common()
        self._load_domain(domain)

    def _load_common(self) -> None:
        """Load the cross-domain exclusion list."""
        self._load_yaml(Path(__file__).parent / "glossaries" / "_common.yaml")

    def _load_domain(self, domain: str) -> None:
        """Load a domain-specific whitelist (non-existent domain is OK)."""
        self._load_yaml(Path(__file__).parent / "glossaries" / f"{domain}.yaml")

    def _load_yaml(self, path: Path) -> None:
        if not path.exists():
            return
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not data or "entries" not in data:
            return
        for e in data["entries"]:
            entry = GlossaryEntry(
                flat=e["flat"],
                latex=e.get("latex"),
                desc=e.get("desc", ""),
                type=e.get("type", "subscript"),
            )
            # Domain entries override common entries for the same flat key
            self.entries[entry.flat] = entry

    def lookup(self, flat: str) -> Optional[GlossaryEntry]:
        """Exact case-sensitive lookup."""
        return self.entries.get(flat)

    def fuzzy_lookup(self, flat: str) -> Optional[GlossaryEntry]:
        """Case-insensitive lookup, but never return exclude entries via fuzzy."""
        entry = self.entries.get(flat)
        if entry is not None:
            return entry if entry.type != "exclude" else None
        # Fallback: case-insensitive scan
        lower = flat.lower()
        for key, entry in self.entries.items():
            if key.lower() == lower and entry.type != "exclude":
                return entry
        return None

    def effective_entries(self) -> list[GlossaryEntry]:
        """Return all non-exclude entries (for LLM prompt)."""
        return [e for e in self.entries.values() if e.type != "exclude" and e.latex]


def glossary_refine(
    candidates: list[Candidate],
    glossary: Glossary,
) -> tuple[list[Candidate], list[Candidate]]:
    """Stage 2: Disambiguate candidates using the domain whitelist.

    Returns (confirmed, pending_llm):
      - confirmed: candidates with proposed_latex filled from glossary
      - pending_llm: candidates not in glossary, needing LLM refinement
    """
    confirmed: list[Candidate] = []
    pending_llm: list[Candidate] = []

    for c in candidates:
        # Try exact match first, then fuzzy
        entry = glossary.lookup(c.original) or glossary.fuzzy_lookup(c.original)

        if entry is None:
            # Not in glossary → needs LLM
            pending_llm.append(c)
        elif entry.type == "exclude":
            # Explicitly excluded → drop
            continue
        elif entry.latex:
            # Hit with LaTeX → confirm
            c.proposed_latex = f"${entry.latex}$"
            c.confidence = 0.99
            c.source = MatchSource.GLOSSARY
            c.rule_name = f"glossary:{entry.desc}"
            confirmed.append(c)
        else:
            # Hit but no LaTeX (shouldn't happen for non-exclude, but handle)
            pending_llm.append(c)

    return confirmed, pending_llm


# ─────────────────────────────────────────────
# Stage 3: LLM Context Refinement
# ─────────────────────────────────────────────

_LLM_PROMPT_TEMPLATE = """\
你是一位学术论文领域的 LaTeX 排版专家。

## 任务
请检查以下文本中的"疑似变量/上下标"片段，结合领域术语词表，判断每个片段是：
A) 物理变量/参数 → 给出标准 LaTeX 格式还原
B) 普通英文单词 → 标记为 skip

## 领域术语词表
{glossary_summary}

## 待检查片段
{candidates_block}

## 规则
1. 变量的文字下标用 _{{\\text{{下标}}}} 格式
2. 变量的数字下标用 _数字 格式
3. 变量的上标用 ^{{上标}} 格式
4. 希腊字母用 \\alpha, \\beta, \\Delta 等 LaTeX 命令
5. 不要修改普通英文单词（如 "the", "and", "input" 等）
6. 每个片段必须给出判断

## 输出格式（严格JSON数组）
```json
[
  {{"original": "Ipd", "verdict": "A", "latex": "I_{{\\text{{pd}}}}"}},
  {{"original": "1n", "verdict": "B", "latex": null}}
]
```"""


async def llm_refine(
    text: str,
    pending: list[Candidate],
    glossary: Glossary,
    llm,  # LLMService instance
) -> list[Candidate]:
    """Stage 3: Use LLM to disambiguate candidates not resolved by glossary.

    Modifies pending candidates in-place, filling proposed_latex for
    those the LLM identifies as variables.
    """
    if not pending or llm is None:
        return pending

    # Build glossary summary for prompt
    effective = glossary.effective_entries()
    glossary_summary = "\n".join(
        f"- {e.flat} → ${e.latex}$ ({e.desc})"
        for e in effective[:60]  # Cap to avoid overly long prompts
    )

    # Build candidates block
    candidates_block = "\n".join(
        f'- 片段: "{c.original}"  上下文: "{c.context}"'
        for c in pending
    )

    prompt = _LLM_PROMPT_TEMPLATE.format(
        glossary_summary=glossary_summary,
        candidates_block=candidates_block,
    )

    try:
        response = await llm.chat(
            prompt,
            system_prompt="你是一位 LaTeX 排版专家，只输出 JSON，不要其他文字。",
        )

        # Strip markdown code fences if present
        response = response.strip()
        if response.startswith("```"):
            lines = response.split("\n")
            # Remove first and last ``` lines
            lines = [l for l in lines if not l.strip().startswith("```")]
            response = "\n".join(lines).strip()

        results = json.loads(response)
        if not isinstance(results, list):
            return pending

        result_map: dict[str, dict] = {r.get("original", ""): r for r in results}

        for c in pending:
            r = result_map.get(c.original)
            if r and r.get("verdict") == "A" and r.get("latex"):
                c.proposed_latex = f"${r['latex']}$"
                c.confidence = 0.92
                c.source = MatchSource.LLM
                c.rule_name = "llm_refine"

    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        # LLM response parsing failed — leave candidates without proposed_latex
        # (QA can detect these unresolved items later)
        pass
    except Exception:  # noqa: BLE001
        # LLM call itself failed — don't crash the pipeline
        pass

    return pending


# ─────────────────────────────────────────────
# Replacement Engine
# ─────────────────────────────────────────────

def apply_replacements(
    text: str,
    candidates: list[Candidate],
    min_confidence: float = 0.70,
) -> tuple[str, list[ReplaceLog]]:
    """Execute text replacements from back to front.

    Only candidates with proposed_latex and confidence >= min_confidence
    are applied. Overlapping intervals are skipped.
    """
    valid = [
        c for c in candidates
        if c.proposed_latex and c.confidence >= min_confidence
    ]
    if not valid:
        return text, []

    # Sort from back to front (to preserve positions)
    valid.sort(key=lambda c: c.start, reverse=True)

    result = text
    log: list[ReplaceLog] = []
    last_end = len(text)  # Track rightmost boundary of already-replaced region

    for c in valid:
        if c.end > last_end:
            # Overlaps with a later (already applied) replacement → skip
            continue
        result = result[:c.start] + c.proposed_latex + result[c.end:]
        log.append(ReplaceLog(
            original=c.original,
            replacement=c.proposed_latex,
            position=c.start,
            source=c.source,
            rule_name=c.rule_name,
            confidence=c.confidence,
        ))
        last_end = c.start

    return result, log


# ─────────────────────────────────────────────
# Main Entry Point
# ─────────────────────────────────────────────

class SubscriptRestorer:
    """Three-stage pipeline: regex → glossary → LLM."""

    def __init__(
        self,
        domain: str = "photonic_ic",
        llm=None,  # LLMService | None
    ) -> None:
        self.glossary = Glossary(domain)
        self.llm = llm

    async def restore(self, text: str) -> RestoredResult:
        """Run the full three-stage pipeline on a text string.

        Returns the restored text and a log of all replacements.
        """
        if not text or not text.strip():
            return RestoredResult(text=text)

        # Stage 1: Regex coarse filter
        t0 = time.monotonic()
        candidates = coarse_filter(text)
        logger.debug("Stage1 正则粗筛: 发现 %d 个候选, 耗时 %.4fs", len(candidates), time.monotonic() - t0)
        if not candidates:
            return RestoredResult(text=text)

        # Stage 2: Glossary refinement
        t0 = time.monotonic()
        confirmed, pending = glossary_refine(candidates, self.glossary)
        logger.debug("Stage2 白名单纠偏: 确认=%d, 待LLM=%d, 耗时 %.4fs",
                     len(confirmed), len(pending), time.monotonic() - t0)

        # Stage 3: LLM refinement (only for unresolved candidates)
        if pending and self.llm is not None:
            t0 = time.monotonic()
            await llm_refine(text, pending, self.glossary, self.llm)
            resolved_by_llm = sum(1 for c in pending if c.proposed_latex)
            logger.info("Stage3 LLM精修: 处理 %d 个待定候选, LLM确认 %d 个, 耗时 %.2fs",
                        len(pending), resolved_by_llm, time.monotonic() - t0)

        # Merge all candidates that have a proposed LaTeX
        all_resolved = confirmed + [c for c in pending if c.proposed_latex]

        # Apply replacements
        result_text, log = apply_replacements(text, all_resolved)
        if log:
            logger.info("上下标还原: 执行 %d 处替换", len(log))

        return RestoredResult(text=result_text, log=log)

    async def restore_blocks(self, blocks) -> None:
        """Pre-processing: restore subscripts in LayoutBlock.text (in-place).

        Args:
            blocks: list of LayoutBlock — .text is modified in-place
        """
        total_replacements = 0
        for block in blocks:
            if not block.translate or not block.text or not block.text.strip():
                continue
            result = await self.restore(block.text)
            if result.log:  # Only update if changes were made
                block.text = result.text
                total_replacements += len(result.log)
        logger.info("预处理(原文): 处理 %d 个可翻译块, 共 %d 处替换",
                    sum(1 for b in blocks if b.translate and b.text.strip()),
                    total_replacements)

    async def restore_translated(self, blocks) -> None:
        """Post-processing: restore subscripts in TranslatedBlock.translated_text (in-place).

        Args:
            blocks: list of TranslatedBlock — .translated_text is modified in-place
        """
        total_replacements = 0
        for block in blocks:
            if not block.translate or not block.translated_text or not block.translated_text.strip():
                continue
            result = await self.restore(block.translated_text)
            if result.log:  # Only update if changes were made
                block.translated_text = result.text
                total_replacements += len(result.log)
        logger.info("后处理(译文): 处理 %d 个已翻译块, 共 %d 处替换",
                    sum(1 for b in blocks if b.translate and b.translated_text.strip()),
                    total_replacements)
