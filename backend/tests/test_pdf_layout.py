import unittest
import tempfile
from pathlib import Path
import fitz
from app.models.schemas import BBox, BlockType, LayoutBlock, TranslatedBlock
from app.services.pdf_composer import PDFComposerService
from app.services.math_layout import translated_html

class PDFLayoutTests(unittest.TestCase):
    def fixture(self, root):
        path = root/'source.pdf'
        with fitz.open() as doc:
            p = doc.new_page(width=400,height=500)
            p.insert_text((30,50),'Academic paper',fontsize=14)
            p.draw_rect(fitz.Rect(220,110,360,200),color=(0,0,1),fill=(.8,.9,1))
            p.insert_text((50,280),'E = mc2',fontsize=12)
            doc.save(path)
        blocks=[LayoutBlock(source_id='title',page=1,type=BlockType.TITLE,bbox=BBox(x0=30,y0=30,x1=300,y1=58),text='Academic paper'),LayoutBlock(source_id='formula',page=1,type=BlockType.FORMULA,bbox=BBox(x0=40,y0=263,x1=150,y1=290),text='E = mc2',translate=False)]
        return path,blocks

    def test_preserves_page_vectors_and_formula(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source,blocks=self.fixture(root)
            result=[TranslatedBlock(source_id='title',page=1,type=BlockType.TITLE,source_text='Academic paper',translated_text='学术论文',translate=True)]
            report={}; output=root/'translated.pdf'
            PDFComposerService().compose(source,output,blocks,result,report=report)
            with fitz.open(source) as before,fitz.open(output) as after:
                self.assertEqual(before[0].rect,after[0].rect)
                self.assertIn('学术论文',after[0].get_text().replace(' ',''))
                self.assertIn('E = mc2',after[0].get_text())
                rect=fitz.Rect(210,100,370,210)
                self.assertEqual(before[0].get_pixmap(clip=rect).samples,after[0].get_pixmap(clip=rect).samples)
            self.assertEqual(report['status'],'passed')

    def test_overflow_retains_source_and_reports_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source,blocks=self.fixture(root)
            result=[TranslatedBlock(source_id='title',page=1,type=BlockType.TITLE,source_text='Academic paper',translated_text='长译文'*2000,translate=True)]
            report={}; output=root/'translated.pdf'
            PDFComposerService().compose(source,output,blocks,result,report=report)
            with fitz.open(output) as doc:
                self.assertIn('Academic paper',doc[0].get_text())
            self.assertEqual(report['status'],'needs_review')
            self.assertEqual(report['retained_blocks'][0]['source_id'],'title')

    def test_inline_math_and_escaped_prose(self):
        html,prose,archive=translated_html('电压 $V_{in,n}-\\alpha_1$ <script> 10⁻¹²',10)
        self.assertIn('<img',html)
        self.assertIn('&lt;script&gt;',html)
        self.assertNotIn('alpha',prose)
        self.assertIn('<sup>',html)
        with fitz.open() as doc:
            p=doc.new_page()
            spare,_=p.insert_htmlbox(fitz.Rect(30,30,300,100),html,archive=archive)
            self.assertGreaterEqual(spare,0)
            self.assertNotIn('alpha',p.get_text())

if __name__=='__main__':
    unittest.main()
