import json
from pathlib import Path
from app.models.schemas import LayoutBlock, TranslateResult
from app.services.pdf_parser import PDFParserService
from app.services.pdf_composer import PDFComposerService
root=Path('data/docs/fa7b729f2a344de3a23c8e5cac569315')
old=[LayoutBlock.model_validate(b) for b in json.loads((root/'layout.json').read_text(encoding='utf-8'))]
fresh=PDFParserService().parse(root/'source.pdf',Path('../tmp/pdfs/assets'))
for b in old:
    candidates=[n for n in fresh if n.page==b.page and n.text==b.text]
    if candidates:
        n=min(candidates,key=lambda n:abs(n.bbox.x0-b.bbox.x0)+abs(n.bbox.y0-b.bbox.y0))
        b.bbox=n.bbox; b.meta=n.meta; b.type=n.type; b.translate=n.translate; b.protected=n.protected
result=TranslateResult.model_validate(json.loads((root/'result.json').read_text(encoding='utf-8')))
report={}
PDFComposerService().compose(root/'source.pdf',Path('../tmp/pdfs/layout-preview.pdf'),old,result.blocks,report=report)
Path('../tmp/pdfs/report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=True))
import fitz
with fitz.open('../tmp/pdfs/layout-preview.pdf') as doc:
    for i in [0,2,5,8,14]:
        doc[i].get_pixmap(matrix=fitz.Matrix(1.5,1.5)).save(f'../tmp/pdfs/page-{i+1}.png')
