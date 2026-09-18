"""Unit tests for page-level vision translation path."""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

import fitz

from app.agents.vision_translator import VisionTranslationAgent, _parse_translations, _soft_validate
from app.models.schemas import BBox, BlockType, LayoutBlock, LLMConfig, TranslatedBlock
from app.services.page_renderer import PageRenderer
from app.services.qa import QAService


class VisionTranslationUnitTests(unittest.TestCase):
    def test_parse_translations_json(self):
        raw = '{"translations":[{"source_id":"p1_b1","translation":"输入电流 I<sub>pd</sub>"}]}'
        mapping = _parse_translations(raw)
        self.assertEqual(mapping["p1_b1"], "输入电流 I<sub>pd</sub>")

    def test_parse_translations_fenced(self):
        raw = '```json\n{"translations":[{"id":"a","text":"你好"}]}\n```'
        self.assertEqual(_parse_translations(raw)["a"], "你好")

    def test_soft_validate_keeps_fig_refs(self):
        err = _soft_validate("See Fig. 2(a) for details.", "详见说明。")
        self.assertIsNotNone(err)
        self.assertIn("Fig", err or "")

    def test_soft_validate_allows_symbol_normalization(self):
        # Flattened source vs visual-aware translation — should not hard-fail on √ alone.
        self.assertIsNone(_soft_validate(
            "Since gm ∝ Id, power drops.",
            "由于 gₘ ∝ √I_d，功耗下降。",
        ))

    def test_page_renderer_writes_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "one.pdf"
            out = Path(tmp) / "page-1.png"
            doc = fitz.open()
            page = doc.new_page()
            page.insert_text((72, 72), "Hello g_m")
            doc.save(pdf)
            doc.close()
            path = PageRenderer().render_page(pdf, 1, out, scale=1.5)
            self.assertTrue(path.exists())
            self.assertGreater(path.stat().st_size, 100)

    def test_vision_agent_maps_source_ids(self):
        blocks = [
            LayoutBlock(
                source_id="p1_b1", page=1, type=BlockType.PARAGRAPH,
                bbox=BBox(x0=50, y0=50, x1=400, y1=100),
                text="Since gm ∝ Id, the power is reduced.",
                translate=True,
            ),
            LayoutBlock(
                source_id="p1_fig", page=1, type=BlockType.FIGURE,
                bbox=BBox(x0=50, y0=200, x1=300, y1=400),
                text="", translate=False, protected=True,
            ),
        ]
        agent = VisionTranslationAgent()
        agent.llm = AsyncMock()
        agent.llm.load_config = lambda: LLMConfig(vision_model="qwen-vl-max")
        agent.llm.chat_with_images = AsyncMock(return_value=json.dumps({
            "translations": [{
                "source_id": "p1_b1",
                "translation": "由于 gₘ ∝ √I_d，功耗降低。",
            }],
        }))

        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "page.png"
            png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
            results = asyncio.run(agent.translate_page(
                blocks, png, source_lang="en", target_lang="zh",
                config=LLMConfig(vision_model="qwen-vl-max"),
            ))

        by_id = {r.source_id: r for r in results}
        self.assertFalse(by_id["p1_fig"].translate)
        self.assertTrue(by_id["p1_b1"].qa.get("vision"))
        self.assertIn("√", by_id["p1_b1"].translated_text)
        agent.llm.chat_with_images.assert_awaited()

    def test_vision_agent_marks_missing_for_fallback(self):
        block = LayoutBlock(
            source_id="p1_b1", page=1, type=BlockType.PARAGRAPH,
            bbox=BBox(x0=50, y0=50, x1=400, y1=100),
            text="Hello world.", translate=True,
        )
        agent = VisionTranslationAgent()
        agent.llm = AsyncMock()
        agent.llm.load_config = lambda: LLMConfig(vision_model="qwen-vl-max")
        agent.llm.chat_with_images = AsyncMock(
            return_value='{"translations":[]}',
        )
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "page.png"
            png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
            results = asyncio.run(agent.translate_page(
                [block], png, source_lang="en", target_lang="zh",
                config=LLMConfig(vision_model="qwen-vl-max"),
            ))
        self.assertTrue(results[0].translated_text.startswith("[翻译失败:"))

    def test_qa_soft_on_vision_symbol_drift(self):
        blocks = [
            TranslatedBlock(
                source_id="p1_b1", page=1, type=BlockType.PARAGRAPH,
                source_text="Since gm ∝ Id",
                translated_text="由于 gₘ ∝ √I_d",
                translate=True,
                qa={"vision": True},
            ),
        ]
        summary = QAService().check(blocks)
        self.assertTrue(summary.formula_fidelity)
        self.assertTrue(summary.overall_pass)
        self.assertTrue(any("软告警" in d for d in summary.details) or summary.overall_pass)

    def test_has_vision_model_detection(self):
        self.assertTrue(LLMConfig(vision_model="qwen-vl-max").has_vision_model())
        self.assertTrue(LLMConfig(model="gpt-4o").has_vision_model())
        self.assertFalse(LLMConfig(model="deepseek-chat").has_vision_model())


if __name__ == "__main__":
    unittest.main()
