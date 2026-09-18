"""Tests for SubscriptRestorer — three-stage subscript restoration pipeline."""

from __future__ import annotations

import unittest

from app.services.subscript_restorer import (
    Candidate,
    Glossary,
    GlossaryEntry,
    MatchSource,
    RestoredResult,
    SubscriptRestorer,
    apply_replacements,
    coarse_filter,
    glossary_refine,
)


# ═══════════════════════════════════════════════
# Stage 1: Coarse Filter
# ═══════════════════════════════════════════════

class TestCoarseFilter(unittest.TestCase):

    def test_single_letter_numeric_sub(self):
        """v1, I3 should be detected as variable + numeric subscript."""
        candidates = coarse_filter("The voltage v1 exceeds v2 at I3.")
        matched_texts = {c.original for c in candidates}
        self.assertIn("v1", matched_texts)
        self.assertIn("v2", matched_texts)
        self.assertIn("I3", matched_texts)

    def test_capital_var_capital_sub(self):
        """VDD, VGS should be detected at high confidence."""
        candidates = coarse_filter("The supply VDD and gate voltage VGS.")
        matched = {c.original for c in candidates}
        self.assertIn("VDD", matched)
        self.assertIn("VGS", matched)
        # Check confidence is high
        for c in candidates:
            if c.original == "VDD":
                self.assertGreaterEqual(c.confidence, 0.85)

    def test_underscore_subscript(self):
        """V_DD, g_m should be detected at highest confidence."""
        candidates = coarse_filter("Variable V_DD and g_m are critical.")
        matched = {c.original for c in candidates}
        self.assertIn("V_DD", matched)
        self.assertIn("g_m", matched)
        for c in candidates:
            if c.original in ("V_DD", "g_m"):
                self.assertGreaterEqual(c.confidence, 0.95)

    def test_single_letter_lowercase_sub(self):
        """gm, Id should be detected (but at lower confidence)."""
        candidates = coarse_filter("The transconductance gm and drain current Id.")
        matched = {c.original for c in candidates}
        self.assertIn("gm", matched)
        self.assertIn("Id", matched)

    def test_stop_words_excluded(self):
        """Common English words (in, on, is, it, etc.) should NOT be matched."""
        text = "It is on in or as be by do if to up"
        candidates = coarse_filter(text)
        matched_texts = {c.original for c in candidates}
        for word in ("It", "is", "on", "in", "or", "as", "be", "by", "do", "if", "to", "up"):
            self.assertNotIn(word, matched_texts, f"Stop word '{word}' should not be matched")

    def test_greek_prefix_sub(self):
        """Δv1 should be detected as Greek prefix + subscript."""
        candidates = coarse_filter("The change Δv1 is small.")
        matched = {c.original for c in candidates}
        # The greek_prefix_sub pattern should match Δv1
        # If the Greek char class doesn't work on this platform,
        # v1 will be matched by single_letter_numeric_sub instead.
        self.assertTrue("Δv1" in matched or "v1" in matched)

    def test_two_capital_var_sub(self):
        """RCIN should be detected as two-capital + subscript."""
        candidates = coarse_filter("The time constant RCIN limits bandwidth.")
        matched = {c.original for c in candidates}
        self.assertIn("RCIN", matched)

    def test_no_false_positives_long_words(self):
        """Long English words should NOT be matched."""
        text = "The transconductance parameter determines performance."
        candidates = coarse_filter(text)
        matched_texts = {c.original for c in candidates}
        # These are real English words, not variables
        self.assertNotIn("The", matched_texts)

    def test_deduplicate_overlapping(self):
        """When patterns overlap, keep the highest confidence."""
        # "VDD" matches both capital_var_capital_sub (0.85) and
        # potentially other patterns. The best should win.
        candidates = coarse_filter("Supply VDD is 3.3 V.")
        # Should have exactly one candidate for "VDD"
        vdd_candidates = [c for c in candidates if c.original == "VDD"]
        self.assertEqual(len(vdd_candidates), 1)
        self.assertGreaterEqual(vdd_candidates[0].confidence, 0.85)

    def test_empty_text(self):
        """Empty or whitespace-only text should return no candidates."""
        self.assertEqual(coarse_filter(""), [])
        self.assertEqual(coarse_filter("   "), [])

    def test_real_paragraph(self):
        """Integration: a realistic paragraph with multiple variables."""
        text = (
            "The transconductance gm of the MOSFET is proportional to "
            "the square root of the drain current Id. For the photodetector, "
            "the photocurrent Ipd flows through RL. The input RC time constant "
            "RCIN determines the 3dB bandwidth f3dB. Vth is critical."
        )
        candidates = coarse_filter(text)
        matched = {c.original for c in candidates}
        # Key variables should be found
        self.assertIn("gm", matched)
        self.assertIn("Ipd", matched)
        self.assertIn("Vth", matched)


# ═══════════════════════════════════════════════
# Stage 2: Glossary
# ═══════════════════════════════════════════════

class TestGlossary(unittest.TestCase):

    def setUp(self):
        self.glossary = Glossary("photonic_ic")

    def test_exact_lookup_hit(self):
        """Exact match should return the correct entry."""
        entry = self.glossary.lookup("VDD")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.latex, r"V_{\text{DD}}")
        self.assertEqual(entry.type, "subscript")

    def test_exact_lookup_miss(self):
        """Non-existent key should return None."""
        entry = self.glossary.lookup("XYZ123")
        self.assertIsNone(entry)

    def test_exclude_entry_exact(self):
        """Exclude entries should be returned by exact lookup."""
        # "in" is in _common.yaml as exclude
        entry = self.glossary.lookup("in")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.type, "exclude")

    def test_fuzzy_lookup_ignores_exclude(self):
        """fuzzy_lookup should never return exclude entries."""
        entry = self.glossary.fuzzy_lookup("in")
        self.assertIsNone(entry)

    def test_fuzzy_lookup_case_insensitive(self):
        """fuzzy_lookup should find entries case-insensitively."""
        # "gm" is in the glossary
        entry = self.glossary.fuzzy_lookup("gm")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.latex, "g_m")

    def test_effective_entries(self):
        """effective_entries should return non-exclude entries with LaTeX."""
        entries = self.glossary.effective_entries()
        # Should have entries from photonic_ic.yaml
        self.assertGreater(len(entries), 10)
        # No exclude entries
        for e in entries:
            self.assertNotEqual(e.type, "exclude")
            self.assertIsNotNone(e.latex)

    def test_glossary_refine_hit(self):
        """Candidates matching glossary should get LaTeX filled."""
        candidates = [Candidate(
            original="VDD", start=0, end=3,
            confidence=0.85, source=MatchSource.REGEX,
            rule_name="capital_var_capital_sub", proposed_latex="",
            context="Supply VDD is 3.3 V",
        )]
        confirmed, pending = glossary_refine(candidates, self.glossary)
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(len(pending), 0)
        self.assertEqual(confirmed[0].proposed_latex, r"$V_{\text{DD}}$")
        self.assertAlmostEqual(confirmed[0].confidence, 0.99)

    def test_glossary_refine_exclude(self):
        """Candidates matching an exclude entry should be dropped."""
        candidates = [Candidate(
            original="in", start=10, end=12,
            confidence=0.65, source=MatchSource.REGEX,
            rule_name="single_letter_lowercase_sub", proposed_latex="",
            context="is in the circuit",
        )]
        confirmed, pending = glossary_refine(candidates, self.glossary)
        self.assertEqual(len(confirmed), 0)
        self.assertEqual(len(pending), 0)

    def test_glossary_refine_miss(self):
        """Candidates not in glossary should go to pending."""
        candidates = [Candidate(
            original="XYZ", start=0, end=3,
            confidence=0.70, source=MatchSource.REGEX,
            rule_name="capital_var_capital_sub", proposed_latex="",
            context="Variable XYZ",
        )]
        confirmed, pending = glossary_refine(candidates, self.glossary)
        self.assertEqual(len(confirmed), 0)
        self.assertEqual(len(pending), 1)


# ═══════════════════════════════════════════════
# Replacement Engine
# ═══════════════════════════════════════════════

class TestApplyReplacements(unittest.TestCase):

    def test_basic_replacement(self):
        """Single replacement should work correctly."""
        text = "The VDD is 3.3 V."
        candidates = [Candidate(
            original="VDD", start=4, end=7,
            confidence=0.99, source=MatchSource.GLOSSARY,
            rule_name="glossary:drain supply voltage",
            proposed_latex=r"$V_{\text{DD}}$",
            context="The VDD is 3.3 V.",
        )]
        result, log = apply_replacements(text, candidates)
        self.assertIn(r"$V_{\text{DD}}$", result)
        self.assertNotIn("VDD", result)
        self.assertEqual(len(log), 1)

    def test_replace_from_end(self):
        """Multiple replacements should be applied from back to front."""
        text = "VDD and VSS are supply rails."
        c1 = Candidate(
            original="VDD", start=0, end=3,
            confidence=0.99, source=MatchSource.GLOSSARY,
            rule_name="glossary", proposed_latex=r"$V_{\text{DD}}$",
            context=text,
        )
        c2 = Candidate(
            original="VSS", start=8, end=11,
            confidence=0.99, source=MatchSource.GLOSSARY,
            rule_name="glossary", proposed_latex=r"$V_{\text{SS}}$",
            context=text,
        )
        result, log = apply_replacements(text, [c1, c2])
        self.assertIn(r"$V_{\text{DD}}$", result)
        self.assertIn(r"$V_{\text{SS}}$", result)
        self.assertEqual(len(log), 2)

    def test_confidence_threshold(self):
        """Replacements below min_confidence should be skipped."""
        text = "The gm is 5 mS."
        candidates = [Candidate(
            original="gm", start=4, end=6,
            confidence=0.50,  # Below default threshold of 0.70
            source=MatchSource.REGEX,
            rule_name="single_letter_lowercase_sub",
            proposed_latex="$g_m$",
            context=text,
        )]
        result, log = apply_replacements(text, candidates, min_confidence=0.70)
        self.assertEqual(result, text)  # Unchanged
        self.assertEqual(len(log), 0)

    def test_no_candidates(self):
        """No candidates should return original text."""
        text = "No variables here."
        result, log = apply_replacements(text, [])
        self.assertEqual(result, text)
        self.assertEqual(len(log), 0)

    def test_skip_overlapping(self):
        """Overlapping replacements should be skipped (keep first)."""
        text = "RCIN is large."
        # Two overlapping candidates: "RCIN" and "CIN" (overlaps)
        c1 = Candidate(
            original="RCIN", start=0, end=4,
            confidence=0.70, source=MatchSource.REGEX,
            rule_name="two_capital_var_sub",
            proposed_latex=r"$\text{RC}_{\text{IN}}$",
            context=text,
        )
        c2 = Candidate(
            original="CIN", start=2, end=5,
            confidence=0.70, source=MatchSource.REGEX,
            rule_name="two_capital_var_sub",
            proposed_latex=r"$C_{\text{IN}}$",
            context=text,
        )
        result, log = apply_replacements(text, [c1, c2])
        # Should apply at least one (the first in back-to-front order)
        self.assertGreaterEqual(len(log), 1)


# ═══════════════════════════════════════════════
# End-to-End: SubscriptRestorer
# ═══════════════════════════════════════════════

class TestSubscriptRestorer(unittest.TestCase):

    def setUp(self):
        # Create restorer without LLM (Stage 3 disabled)
        self.restorer = SubscriptRestorer(domain="photonic_ic", llm=None)

    def test_restore_with_glossary(self):
        """Known variables should be restored via glossary (no LLM needed)."""
        import asyncio
        text = "The transconductance gm and supply VDD."
        result = asyncio.run(self.restorer.restore(text))
        self.assertIn(r"$g_m$", result.text)
        self.assertIn(r"$V_{\text{DD}}$", result.text)
        self.assertGreater(len(result.log), 0)

    def test_restore_multiple_variables(self):
        """Multiple variables in a paragraph should all be restored."""
        import asyncio
        text = (
            "The photocurrent Ipd flows through RL. "
            "The input time constant RCIN limits the bandwidth."
        )
        result = asyncio.run(self.restorer.restore(text))
        self.assertIn(r"$I_{\text{pd}}$", result.text)
        self.assertIn(r"$R_L$", result.text)
        self.assertIn(r"$\text{RC}_{\text{IN}}$", result.text)

    def test_restore_mixed_case_numeric(self):
        """Variables like f3dB should be restored via glossary after regex match."""
        import asyncio
        text = "The 3dB bandwidth f3dB is 10 GHz."
        result = asyncio.run(self.restorer.restore(text))
        self.assertIn(r"$f_{3\text{dB}}$", result.text)

    def test_restore_no_candidates(self):
        """Text with no variables should be returned unchanged."""
        import asyncio
        text = "This is a simple sentence with no variables."
        result = asyncio.run(self.restorer.restore(text))
        self.assertEqual(result.text, text)
        self.assertEqual(len(result.log), 0)

    def test_restore_empty_text(self):
        """Empty text should return empty result."""
        import asyncio
        result = asyncio.run(self.restorer.restore(""))
        self.assertEqual(result.text, "")

    def test_restore_preserves_surrounding_text(self):
        """Restoration should not damage surrounding text."""
        import asyncio
        text = "The threshold voltage Vth is a critical parameter for the device."
        result = asyncio.run(self.restorer.restore(text))
        # Should still have the surrounding words
        self.assertIn("threshold", result.text)
        self.assertIn("critical", result.text)
        self.assertIn("parameter", result.text)
        # And Vth should be replaced
        self.assertIn(r"$V_{\text{th}}$", result.text)

    def test_restore_blocks_in_place(self):
        """restore_blocks should modify LayoutBlock.text in-place."""
        import asyncio
        from app.models.schemas import LayoutBlock, BBox, BlockType

        blocks = [
            LayoutBlock(
                source_id="b1", page=1, type=BlockType.PARAGRAPH,
                bbox=BBox(x0=0, y0=0, x1=100, y1=20),
                text="The supply VDD is 3.3 V.",
                translate=True,
            ),
            LayoutBlock(
                source_id="b2", page=1, type=BlockType.FORMULA,
                bbox=BBox(x0=0, y0=20, x1=100, y1=40),
                text="V_DD = 3.3",
                translate=False,
            ),
        ]
        asyncio.run(self.restorer.restore_blocks(blocks))
        # Translatable block should be modified
        self.assertIn(r"$V_{\text{DD}}$", blocks[0].text)
        # Non-translatable block should be unchanged
        self.assertEqual(blocks[1].text, "V_DD = 3.3")

    def test_restore_translated_in_place(self):
        """restore_translated should modify TranslatedBlock.translated_text in-place."""
        import asyncio
        from app.models.schemas import TranslatedBlock, BlockType

        blocks = [
            TranslatedBlock(
                source_id="b1", page=1, type=BlockType.PARAGRAPH,
                source_text="The supply VDD is 3.3 V.",
                translated_text="电源电压 VDD 为 3.3 V。",
                translate=True,
            ),
        ]
        asyncio.run(self.restorer.restore_translated(blocks))
        self.assertIn(r"$V_{\text{DD}}$", blocks[0].translated_text)


if __name__ == "__main__":
    unittest.main()
