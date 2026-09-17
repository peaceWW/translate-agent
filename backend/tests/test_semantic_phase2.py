import asyncio
import unittest
from types import SimpleNamespace

from app.models.schemas import BBox, BlockType, LayoutBlock, LLMConfig
from app.services.semantic_builder import (
    SegmentType,
    assemble_segments,
    build_semantic_units,
    split_into_segments,
)
from app.services.structured_translator import StructuredTranslator


class SemanticBuilderTests(unittest.TestCase):
    def test_split_keeps_fig_and_math_as_non_text(self):
        segs = split_into_segments(
            'Gain gm ∝ √Id rises as shown in Fig. 4 and reaches −7.8 dBm.'
        )
        types = [s.type for s in segs]
        self.assertIn(SegmentType.FIG_REF, types)
        self.assertTrue(any(s.type == SegmentType.TEXT for s in segs))
        self.assertIn('Fig. 4', [s.source for s in segs if s.type == SegmentType.FIG_REF])

    def test_assemble_uses_text_translations_only(self):
        segs = split_into_segments('See Fig. 1 for details.')
        for seg in segs:
            if seg.type == SegmentType.TEXT:
                seg.translation = '详见'
        # Fig ref stays English source
        out = assemble_segments(segs)
        self.assertIn('Fig. 1', out)
        self.assertIn('详见', out)

    def test_build_units_merges_across_formula_scrap(self):
        blocks = [
            LayoutBlock(
                source_id='a', page=5, type=BlockType.PARAGRAPH,
                bbox=BBox(x0=50, y0=100, x1=250, y1=140),
                text='Bandwidth is reduced and gm can be halved. Since gm ∝',
                translate=True,
            ),
            LayoutBlock(
                source_id='f', page=5, type=BlockType.FORMULA,
                bbox=BBox(x0=50, y0=142, x1=120, y1=160),
                text='√Id',
                translate=False, protected=True,
            ),
            LayoutBlock(
                source_id='c', page=5, type=BlockType.PARAGRAPH,
                bbox=BBox(x0=50, y0=162, x1=250, y1=200),
                text='the power can be reduced to one quarter.',
                translate=True,
            ),
        ]
        units = build_semantic_units(blocks)
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0].primary_source_id, 'a')
        self.assertEqual(units[0].absorb_source_ids, ['c'])
        self.assertTrue(any(s.type == SegmentType.MATH and '√' in s.source for s in units[0].segments))


class StructuredTranslatorTests(unittest.TestCase):
    def test_structured_unit_translation_assembles_json(self):
        block = LayoutBlock(
            source_id='p1', page=1, type=BlockType.PARAGRAPH,
            bbox=BBox(x0=0, y0=0, x1=200, y1=40),
            text='The gain is shown in Fig. 4 at −7.8 dBm.',
            translate=True,
        )

        async def chat(prompt, **kwargs):
            from app.services.semantic_builder import split_into_segments, text_segments_for_prompt, SemanticUnit
            unit = SemanticUnit(
                unit_id='x', page=1, primary_source_id='p1', member_source_ids=['p1'],
                segments=split_into_segments(block.text), source_text=block.text,
            )
            texts = text_segments_for_prompt(unit)
            items = []
            for i, t in enumerate(texts):
                zh = ['增益示于', '，条件为', '。'][i] if i < 3 else '…'
                items.append({"id": t["id"], "translation": zh})
            return '{"translations":' + __import__('json').dumps(items, ensure_ascii=False) + '}'

        llm = SimpleNamespace(chat=chat, load_config=lambda: LLMConfig())
        result = asyncio.run(StructuredTranslator(llm).translate_block_structured(
            block, source_lang='en', target_lang='zh', config=LLMConfig()))
        self.assertIn('Fig. 4', result.translated_text)
        self.assertIn('−7.8 dBm', result.translated_text)
        self.assertIn('增益', result.translated_text)

    def test_page_units_emit_absorb_marker(self):
        blocks = [
            LayoutBlock(
                source_id='a', page=5, type=BlockType.PARAGRAPH,
                bbox=BBox(x0=50, y0=100, x1=250, y1=140),
                text='Since gm can be halved because',
                translate=True,
            ),
            LayoutBlock(
                source_id='f', page=5, type=BlockType.FORMULA,
                bbox=BBox(x0=50, y0=142, x1=120, y1=160),
                text='gm ∝ √Id',
                translate=False, protected=True,
            ),
            LayoutBlock(
                source_id='c', page=5, type=BlockType.PARAGRAPH,
                bbox=BBox(x0=50, y0=162, x1=250, y1=200),
                text='power drops.',
                translate=True,
            ),
        ]

        async def chat(prompt, **kwargs):
            import json
            import re
            texts_start = prompt.find('待翻译 text 节点：')
            chunk = prompt[texts_start:] if texts_start >= 0 else prompt
            text_ids = re.findall(r'"id":\s*"([^"]+)"', chunk.split('排版约束')[0])
            items = [{"id": i, "translation": "译文"} for i in dict.fromkeys(text_ids)]
            if not items:
                items = [{"id": "U0A0", "translation": "由于可以减半，因为"}, {"id": "U0B0", "translation": "功耗下降。"}]
            return json.dumps({"translations": items}, ensure_ascii=False)

        llm = SimpleNamespace(chat=chat, load_config=lambda: LLMConfig())
        results = asyncio.run(StructuredTranslator(llm).translate_page_units(
            blocks, source_lang='en', target_lang='zh', config=LLMConfig()))
        by_id = {r.source_id: r for r in results}
        self.assertTrue(by_id['c'].translated_text.startswith('[MERGED_INTO:'))
        self.assertIn('a', by_id['c'].translated_text)
        self.assertFalse(by_id['a'].translated_text.startswith('['))


if __name__ == '__main__':
    unittest.main()
