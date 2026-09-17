import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.agents.page_repair import PageRepairAgent
from app.models.schemas import BBox, BlockType, LayoutBlock, LLMConfig, TranslatedBlock


class PageRepairTests(unittest.TestCase):
    def test_repair_replaces_failed_blocks_using_page_context(self):
        blocks = [
            LayoutBlock(source_id='a', page=1, type=BlockType.PARAGRAPH,
                        bbox=BBox(x0=0, y0=0, x1=100, y1=20), text='Hello world.', translate=True),
            LayoutBlock(source_id='b', page=1, type=BlockType.PARAGRAPH,
                        bbox=BBox(x0=0, y0=30, x1=100, y1=50),
                        text='Gain is Δv ≤ 2 μV in Fig. 4.', translate=True),
        ]
        results = [
            TranslatedBlock(source_id='a', page=1, type=BlockType.PARAGRAPH,
                            source_text=blocks[0].text, translated_text='你好世界。', translate=True),
            TranslatedBlock(source_id='b', page=1, type=BlockType.PARAGRAPH,
                            source_text=blocks[1].text,
                            translated_text='[翻译失败: 模型遗漏或重复了公式、图注编号或科学符号]',
                            translate=True),
        ]
        payload = '[{"source_id":"b","translated_text":"增益为 Δv ≤ 2 μV，见图 Fig. 4。"}]'
        llm = SimpleNamespace(chat=AsyncMock(return_value=payload), load_config=lambda: LLMConfig())
        updated, count = asyncio.run(PageRepairAgent(llm).repair_page(
            blocks, results, source_lang='en', target_lang='zh', config=LLMConfig()))
        self.assertEqual(count, 1)
        self.assertEqual(updated[1].translated_text, '增益为 Δv ≤ 2 μV，见图 Fig. 4。')
        self.assertIn('FAILED', llm.chat.await_args.args[0])

    def test_repair_skips_symbol_regressions(self):
        blocks = [
            LayoutBlock(source_id='b', page=1, type=BlockType.PARAGRAPH,
                        bbox=BBox(x0=0, y0=0, x1=100, y1=20),
                        text='Δv ≤ 2 μV', translate=True),
        ]
        results = [
            TranslatedBlock(source_id='b', page=1, type=BlockType.PARAGRAPH,
                            source_text=blocks[0].text,
                            translated_text='[翻译失败: x]', translate=True),
        ]
        payload = '[{"source_id":"b","translated_text":"增益 2 μV"}]'  # lost Δ and ≤
        llm = SimpleNamespace(chat=AsyncMock(return_value=payload), load_config=lambda: LLMConfig())
        updated, count = asyncio.run(PageRepairAgent(llm).repair_page(
            blocks, results, source_lang='en', target_lang='zh', config=LLMConfig()))
        self.assertEqual(count, 0)
        self.assertTrue(updated[0].translated_text.startswith('[翻译失败:'))


if __name__ == '__main__':
    unittest.main()
