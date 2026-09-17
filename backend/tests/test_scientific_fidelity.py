import asyncio
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
import fitz

from app.agents.translator import TranslationAgent
from app.models.schemas import BBox, BlockType, LayoutBlock, LLMConfig, TranslatedBlock
from app.services.math_layout import translated_html
from app.services.pdf_composer import PDFComposerService
from app.services.pdf_parser import PDFParserService
from app.services.pdf_text import font_control_maps
from app.services.protection import (
    compare_protected_tokens,
    compare_scientific_symbols,
    looks_like_formula,
    mask_scientific_content,
    merge_adjacent_script_tags,
    missing_figure_table_refs,
    restore_scientific_content,
)
from app.services.qa import QAService


class ScientificFidelityTests(unittest.TestCase):
    def test_wordy_formula_is_not_prose(self):
        for text in ['SFDR = 2/3 (IIP3(dBm) − Noise power(dBm)). (23)',
                     'SFDR =\n2\n3 (IIP3(dBm) − Noise power(dBm)).\n(23)',
                     'Vout = Gain × Vin (2)']:
            self.assertTrue(looks_like_formula(text), text)
        for text in ['The SFDR = 70 dB in this experiment.',
                     'Fig. 1. Output power at V = 1 V.',
                     'where Ipd is the peak PD current and Tb is the bit period.']:
            self.assertFalse(looks_like_formula(text), text)

    def test_scientific_atoms_restore_exactly_and_reject_losses(self):
        source = 'Fig. 1. (a) Δv ≤ 0.07 dB, ±2 μV, I<sub>pd</sub>, 10⁻¹², Ω and $x^2$.'
        masked, mapping = mask_scientific_content(source)
        self.assertNotIn('0.07', masked)
        self.assertNotIn('Δ', masked)
        self.assertEqual(restore_scientific_content(masked, mapping), source)
        key = next(iter(mapping))
        for broken in [masked.replace(key, ''), masked + key]:
            with self.assertRaises(ValueError):
                restore_scientific_content(broken, mapping)
        # Hallucinated markers are stripped so one bad token does not kill the paragraph.
        self.assertEqual(
            restore_scientific_content(masked + '[[KEEP_deadbeef_99]]', mapping),
            source,
        )

    def test_english_dates_are_masked_as_whole_phrases(self):
        source = 'Manuscript received December 1, 2019; revised May 13, 2020 and July 16,\n2020.'
        masked, mapping = mask_scientific_content(source)
        self.assertNotIn('December', masked)
        self.assertTrue(any(v.startswith('December') for v in mapping.values()))
        # Day/year must not be frozen alone next to a free month name.
        self.assertFalse(any(v in {'1', '2019', '13', '2020', '16'} for v in mapping.values()))
        restored = restore_scientific_content(masked, mapping)
        self.assertIn('December 1, 2019', restored)
        self.assertIn('May 13, 2020', restored)

    def test_postal_and_bare_integers_are_not_frozen(self):
        masked, mapping = mask_scientific_content('QC H3A 0E9, Canada; ratio 1:4; year 2019 alone')
        self.assertIn('0E9', masked)
        self.assertIn('1:4', masked)
        self.assertIn('2019', masked)
        self.assertFalse(any(v in {'0E9', '1', '4', '2019'} for v in mapping.values()))

    def test_model_retries_missing_caption_label_then_restores(self):
        block = LayoutBlock(source_id='caption',page=1,type=BlockType.CAPTION,
            bbox=BBox(x0=20,y0=20,x1=500,y1=60),text='Fig. 1. (a) Gain is 0.07 dB at Δv ≤ 2 μV.')
        calls = []
        async def chat(prompt, **kwargs):
            calls.append(prompt)
            if len(calls) == 1:
                return '图注遗漏'
            source = prompt.split('原文：\n',1)[1].split('\n上次输出',1)[0].strip()
            return source.replace('Gain is', '增益为').replace(' at ', '，条件为 ')
        agent = TranslationAgent(SimpleNamespace(chat=chat))
        result = asyncio.run(agent.translate_block(block,source_lang='en',target_lang='zh',config=LLMConfig()))
        self.assertEqual(len(calls), 2)
        self.assertIn('Fig. 1. (a)',result.translated_text)
        self.assertIn('0.07 dB',result.translated_text)
        self.assertIn('Δv ≤ 2 μV',result.translated_text)
        self.assertTrue(QAService().check([result]).overall_pass)

    def test_repeated_protection_failure_is_soft_marked(self):
        llm = SimpleNamespace(chat=AsyncMock(return_value='内容不完整'))
        block = LayoutBlock(source_id='x',page=1,type=BlockType.PARAGRAPH,
            bbox=BBox(x0=0,y0=0,x1=200,y1=40),text='Δv ≤ 2 μV')
        result = asyncio.run(TranslationAgent(llm).translate_block(block,source_lang='en',target_lang='zh',config=LLMConfig()))
        # 2 masked attempts + 1 unmasked fallback
        self.assertEqual(llm.chat.await_count, 3)
        self.assertTrue(result.translated_text.startswith('[翻译失败:'))
        self.assertFalse(QAService().check([result]).overall_pass)

    def test_localized_figure_refs_are_accepted(self):
        source = 'Results in Fig. 10(a), Fig. 10(c) and Fig. 9(b) follow Fig. 3.'
        target = '结果见 图 10(a)、图10(c) 与 Fig. 9(b)，同图 3。'
        self.assertEqual(missing_figure_table_refs(source, target), [])
        ok, missing = compare_protected_tokens(source, target)
        self.assertTrue(ok, missing)

    def test_display_equation_snippets_look_like_formula(self):
        self.assertTrue(looks_like_formula('kT/C_IN\n=\nI_pd T_b'))
        self.assertTrue(looks_like_formula('(6)'))
        self.assertTrue(looks_like_formula('Δv_0.75Tb = 2 R_pd P_avg / C_P'))
        self.assertFalse(looks_like_formula('where Ipd is the peak PD current and Tb is the bit period.'))

    def test_unicode_subscript_digits_do_not_fail_symbol_check(self):
        source = 'improvement in Δv over v0.75Tb is shown in Fig. 4'
        target = 'Δv 相对 v₀.₇₅Tb 的改善见图 Fig. 4'
        ok, added, removed = compare_scientific_symbols(source, target)
        self.assertTrue(ok, (added, removed))

    def test_unmasked_fallback_recovers_after_keep_failures(self):
        block = LayoutBlock(source_id='p',page=1,type=BlockType.PARAGRAPH,
            bbox=BBox(x0=0,y0=0,x1=400,y1=80),
            text='The receiver achieves −7.8 dBm at 22 Gb/s as shown in Fig. 10.')
        calls = []
        async def chat(prompt, **kwargs):
            calls.append(prompt)
            if '[[KEEP_' in prompt.split('原文：\n',1)[-1].split('\n上次',1)[0]:
                return '漏掉标记的译文'
            return '该接收机在 22 Gb/s 下达到 −7.8 dBm，如 Fig. 10 所示。'
        result = asyncio.run(TranslationAgent(SimpleNamespace(chat=chat)).translate_block(
            block, source_lang='en', target_lang='zh', config=LLMConfig()))
        self.assertEqual(len(calls), 3)
        self.assertFalse(result.translated_text.startswith('[翻译失败:'))
        self.assertIn('−7.8 dBm', result.translated_text)
        self.assertIn('Fig. 10', result.translated_text)

    def test_symbol_change_is_detected_in_cached_translation(self):
        result = TranslatedBlock(source_id='x',page=1,type=BlockType.PARAGRAPH,
            source_text='Δv ≤ 2 μV',translated_text='Δv ≥ 2 μV',translate=True)
        self.assertFalse(QAService().check([result]).formula_fidelity)

    def test_chinese_extra_middot_times_degree_are_allowed(self):
        source = 'Gain is 0.07 dB at Δv ≤ 2 μV for 1:4 demux.'
        target = '增益为 0.07 dB，条件为 Δv ≤ 2 μV，用于 1×4·解复用（约 65°）。'
        ok, added, removed = compare_scientific_symbols(source, target)
        self.assertTrue(ok, (added, removed))
        qa = QAService().check([TranslatedBlock(
            source_id='x', page=1, type=BlockType.PARAGRAPH,
            source_text=source, translated_text=target, translate=True)])
        self.assertTrue(qa.formula_fidelity)

    def test_lost_greek_or_relation_still_fails(self):
        ok, added, removed = compare_scientific_symbols('Δv ≤ 2 μV', '增益 2 μV')
        self.assertFalse(ok)
        self.assertIn('Δ', removed)
        self.assertIn('≤', removed)

    def test_adjacent_script_tags_are_merged_before_mask(self):
        source = 'BER < 10<sup>−</sup><sup>12</sup> at −7.8 dBm'
        self.assertEqual(merge_adjacent_script_tags(source), 'BER < 10<sup>−12</sup> at −7.8 dBm')
        masked, mapping = mask_scientific_content(source)
        self.assertEqual(len([v for v in mapping.values() if '10<sup>' in v]), 1)
        self.assertIn('10<sup>−12</sup>', mapping.values())
        restored = restore_scientific_content(masked, mapping)
        self.assertEqual(restored, 'BER < 10<sup>−12</sup> at −7.8 dBm')
        ok, _, _ = compare_scientific_symbols(merge_adjacent_script_tags(source), restored)
        self.assertTrue(ok)

    def test_retry_prompt_includes_symbol_diff(self):
        block = LayoutBlock(source_id='caption',page=1,type=BlockType.CAPTION,
            bbox=BBox(x0=20,y0=20,x1=500,y1=60),text='Fig. 1. Δv ≤ 2 μV.')
        calls = []
        async def chat(prompt, **kwargs):
            calls.append(prompt)
            source = prompt.split('原文：\n',1)[1].split('\n上次输出',1)[0].strip()
            if len(calls) == 1:
                # Invent a strict symbol while keeping all KEEP markers intact.
                return '∑ ' + source
            return source
        agent = TranslationAgent(SimpleNamespace(chat=chat))
        result = asyncio.run(agent.translate_block(block,source_lang='en',target_lang='zh',config=LLMConfig()))
        self.assertEqual(len(calls), 2)
        self.assertIn('多余', calls[1])
        self.assertIn('∑', calls[1])
        self.assertIn('Δv ≤ 2 μV', result.translated_text)

    def test_one_byte_cmap_bfchar_is_recovered_from_glyph_name(self):
        doc = SimpleNamespace(
            xref_get_key=lambda x,k: ('xref','2 0 R' if k=='Encoding' else '3 0 R'),
            xref_object=lambda x: '<< /Differences [3 /Delta1] >>',
            xref_stream=lambda x: b'1 beginbfchar <03> <0003> endbfchar')
        page = SimpleNamespace(parent=doc,get_fonts=lambda:[(1,'cff','Type1','ABC+MathFont','F1','')])
        self.assertEqual(font_control_maps(page)['MathFont'][3], 'Δ')

    def test_quantity_gap_is_fixed_and_cannot_wrap(self):
        html, _, archive = translated_html('测得增益0.07     dB，随后继续测量并记录所有实验结果。', 12)
        self.assertIn('0.07&#160;dB', html)
        with fitz.open() as doc:
            page = doc.new_page(width=300,height=250)
            page.insert_htmlbox(fitz.Rect(20,20,215,180), html,
                css='body {font-size:12pt;text-align:justify}',archive=archive)
            number = page.search_for('0.07')[0]
            unit = page.search_for('dB')[0]
            self.assertLess(abs(number.y0-unit.y0), 1)
            self.assertGreaterEqual(unit.x0-number.x1, 0)
            self.assertLess(unit.x0-number.x1, 5)

    def test_formula_pixels_and_edge_caption_survive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with fitz.open() as doc:
                page = doc.new_page(width=600,height=800)
                page.insert_text((30,110),'SFDR =',fontsize=12)
                page.insert_text((84,102),'2',fontsize=10)
                page.draw_line((82,105),(92,105))
                page.insert_text((84,116),'3',fontsize=10)
                page.insert_text((96,110),'(IIP3(dBm) - Noise power(dBm)).     (23)',fontsize=12)
                page.insert_text((30,780),'Fig. 1. (a) Receiver. (b) Gain.',fontsize=10)
                doc.save(root/'source.pdf')
            blocks = PDFParserService().parse(root/'source.pdf',root/'assets')
            equations = [b for b in blocks if 'SFDR' in b.text]
            self.assertTrue(equations)
            self.assertTrue(all(not b.translate for b in equations))
            caption = next(b for b in blocks if 'Fig.' in b.text)
            self.assertEqual(caption.type,BlockType.CAPTION)
            self.assertTrue(caption.translate)
            translated = [TranslatedBlock(source_id=caption.source_id,page=1,type=caption.type,
                source_text=caption.text,translated_text='Fig. 1. (a) 接收机。(b) 增益。',translate=True)]
            report = {}
            PDFComposerService().compose(root/'source.pdf',root/'target.pdf',blocks,translated,report=report)
            self.assertEqual(report['retained_blocks'],[])
            with fitz.open(root/'source.pdf') as source,fitz.open(root/'target.pdf') as target:
                clip = fitz.Rect(25,85,550,125)
                self.assertEqual(source[0].get_pixmap(clip=clip).samples,target[0].get_pixmap(clip=clip).samples)
                self.assertIn('接收机',target[0].get_text())

    def test_clipped_curves_do_not_hide_captions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with fitz.open() as doc:
                page = doc.new_page(width=600,height=800)
                page.draw_rect(fitz.Rect(40,50,260,440),color=(0,0,1),fill=(.8,.9,1))
                xref = page.get_contents()[0]
                # Only y=40..220 is visible; the path's un-clipped bbox extends
                # into the caption, as in the user's Fig. 10 plotting curves.
                doc.update_stream(xref,b'q 40 580 220 180 re W n\n'+doc.xref_stream(xref)+b'\nQ')
                page.insert_text((40,430),'Fig. 10. (a) BER curve. (b) Eye diagram.',fontsize=10)
                doc.save(root/'source.pdf')
            blocks = PDFParserService().parse(root/'source.pdf',root/'assets')
            caption = next(b for b in blocks if 'Fig.' in b.text)
            translated = [TranslatedBlock(source_id=caption.source_id,page=1,type=caption.type,
                source_text=caption.text,translated_text='Fig. 10. (a) 误码率曲线。(b) 眼图。',translate=True)]
            report = {}
            PDFComposerService().compose(root/'source.pdf',root/'target.pdf',blocks,translated,report=report)
            self.assertEqual(report['retained_blocks'],[])
            with fitz.open(root/'source.pdf') as before,fitz.open(root/'target.pdf') as after:
                self.assertIn('误码率曲线',after[0].get_text())
                clip=fitz.Rect(35,45,270,225)
                self.assertEqual(before[0].get_pixmap(clip=clip).samples,after[0].get_pixmap(clip=clip).samples)


if __name__ == '__main__':
    unittest.main()
