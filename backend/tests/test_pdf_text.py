import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import fitz
from app.models.schemas import BBox, BlockType, LayoutBlock, TranslatedBlock
from app.services.pdf_text import font_control_maps, line_text, repair_cached_controls, repair_legacy_prose_scripts
from app.services.math_layout import translated_html
from app.services.pdf_composer import PDFComposerService
from app.services.pdf_parser import PDFParserService


class PDFTextTests(unittest.TestCase):
    def test_legacy_prose_scripts_keep_actual_exponents(self):
        self.assertEqual(repair_legacy_prose_scripts(
            '<sup>这是普通的正文</sup>10<sup>−12</sup>', '<sup>This is ordinary prose</sup>'),
            '这是普通的正文10<sup>−12</sup>')

    def test_baseline_and_real_scripts(self):
        spans = [dict(text='where I', size=10, origin=(20, 50), flags=6),
                 dict(text='pd', size=7, origin=(50, 52), flags=6),
                 dict(text=' is 10', size=10, origin=(60, 50), flags=16),
                 dict(text='−12', size=7, origin=(90, 46), flags=1)]
        result = line_text(dict(spans=spans, bbox=(20, 40, 100, 55)), {})
        self.assertEqual(result, 'where I<sub>pd</sub> is 10<sup>−12</sup>')

    def test_font_specific_control_recovery(self):
        doc = SimpleNamespace(
            xref_get_key=lambda x, k: ('xref', '2 0 R' if k == 'Encoding' else '3 0 R'),
            xref_object=lambda x: '<< /Differences [ 3 /Delta1 ] >>',
            xref_stream=lambda x: b'1 beginbfrange <0003> <0003> <0003> endbfrange')
        page = SimpleNamespace(parent=doc, get_fonts=lambda: [(1,'cff','Type1','ABC+RBLMI','F1','')])
        maps = font_control_maps(page)
        self.assertEqual(maps, {'RBLMI': {3: 'Δ'}})
        spans = [dict(text='\x03v', font='RBLMI')]
        self.assertEqual(repair_cached_controls('电压\x03v', spans, maps), '电压Δv')
        spans.append(dict(text='\x03', font='BLEX'))
        self.assertEqual(repair_cached_controls('\x03', spans, maps), '\x03')

    def test_scripts_are_not_missing_characters(self):
        html, prose, _ = translated_html('电流 I<sub>pd</sub> 和 10<sup>−12</sup>，x < 3', 10)
        self.assertEqual(prose, '电流 Ipd 和 10−12，x < 3')
        self.assertIn('&lt; 3', html)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with fitz.open() as doc:
                page = doc.new_page(width=400, height=500)
                page.insert_text((30, 60), 'The peak PD current and bit period.', fontsize=10)
                doc.save(root/'source.pdf')
            block = LayoutBlock(source_id='body', page=1, type=BlockType.PARAGRAPH,
                                bbox=BBox(x0=30,y0=40,x1=370,y1=100), text='The peak PD current and bit period.')
            translation = TranslatedBlock(source_id='body', page=1, type=block.type,
                source_text=block.text, translated_text='电流 I<sub>pd</sub> 和 10<sup>−12</sup>', translate=True)
            report = {}
            PDFComposerService().compose(root/'source.pdf', root/'target.pdf', [block], [translation], report=report)
            self.assertEqual(report['retained_blocks'], [])
            with fitz.open(root/'target.pdf') as doc:
                self.assertIn('电流', doc[0].get_text().replace(' ', ''))
                self.assertNotIn('The peak', doc[0].get_text())
        self.assertFalse(PDFComposerService._contains_text('电流Δ', '电流'))

    def test_equation_fragments_protected_but_prose_translated(self):
        def block(identifier, text, box, kind=BlockType.PARAGRAPH):
            return LayoutBlock(source_id=identifier, page=1, type=kind, text=text,
                bbox=BBox(x0=box[0],y0=box[1],x1=box[2],y1=box[3]), translate=kind != BlockType.FORMULA)
        blocks = [block('eq', 'Δv = RI', (310,100,410,125), BlockType.FORMULA),
                  block('fragment', 'RCIN\n(1)', (405,100,565,125)),
                  block('body', 'where Ipd is the peak PD current.', (310,130,565,150)),
                  block('fraction', 'C<sub>IN</sub> + 4C<sub>s</sub>\nQ<sub>total</sub>\n(5)', (350,200,565,224)),
                  block('tail', '2Δv<sub>max</sub>. (4)', (400,100,560,125)),
                  block('mixed', 'Δv<sub>DOM</sub> = 1', (310,300,400,325)),
                  block('omega', 'ω', (300,100,320,118)),
                  block('kt', 'kT/C<sub>IN</sub>\n\n=\nI<sub>pd</sub>T<sub>b</sub>', (200,100,380,130)),
                  block('eqnum', '(6)', (500,100,540,118))]
        PDFParserService._protect_formula_fragments(blocks)
        PDFParserService._lock_display_equations(blocks)
        self.assertFalse(blocks[1].translate)
        self.assertTrue(blocks[2].translate)
        self.assertFalse(blocks[3].translate)
        self.assertFalse(blocks[4].translate)
        self.assertFalse(blocks[5].translate)
        self.assertFalse(blocks[6].translate)
        self.assertEqual(blocks[6].type, BlockType.FORMULA)
        self.assertFalse(blocks[7].translate)
        self.assertEqual(blocks[7].type, BlockType.FORMULA)
        self.assertFalse(blocks[8].translate)
        self.assertEqual(blocks[8].type, BlockType.FORMULA)


if __name__ == '__main__':
    unittest.main()
