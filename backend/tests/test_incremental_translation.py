import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import fitz
from fastapi import HTTPException
from app.agents.orchestrator import DocumentAgent
from app.api import documents
from app.core.config import Settings
from app.models.schemas import BBox, BlockType, DocumentMeta, LayoutBlock, LLMConfig, TranslateRequest, TranslatedBlock


class IncrementalTranslationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path('tests').resolve())
        self.addCleanup(self.tmp.cleanup)
        self.settings = Settings(data_dir=Path(self.tmp.name))
        with patch('app.agents.orchestrator.get_settings', return_value=self.settings):
            self.agent = DocumentAgent()
        self.doc_id = 'incremental-test'
        self.agent._doc_dir(self.doc_id).mkdir(parents=True)
        self.blocks = []
        with fitz.open() as doc:
            for number in range(1, 4):
                page = doc.new_page(width=400, height=500)
                page.insert_text((30, 50), f'Source paragraph page {number}', fontsize=12)
                self.blocks.append(LayoutBlock(source_id=f'p{number}',page=number,
                    type=BlockType.PARAGRAPH if number < 3 else BlockType.FORMULA,
                    bbox=BBox(x0=30,y0=30,x1=340,y1=70),text=f'Source paragraph page {number}',
                    translate=number < 3))
            doc.save(self.agent.source_pdf_path(self.doc_id))
        self.agent._docs[self.doc_id] = DocumentMeta(doc_id=self.doc_id, filename='paper.pdf',
            page_count=3, created_at='2026-09-17T00:00:00Z')
        self.agent.parser = SimpleNamespace(parse=lambda *a, **kw: self.blocks)
        self.entered_second = asyncio.Event()
        self.continue_second = asyncio.Event()
        self.fail_second = False

        async def translate(block, **kwargs):
            if block.page == 2:
                self.entered_second.set()
                await self.continue_second.wait()
                if self.fail_second:
                    raise RuntimeError('model unavailable on page 2')
            return TranslatedBlock(source_id=block.source_id,page=block.page,type=block.type,
                source_text=block.text,translated_text=f'Translated paragraph page {block.page}',translate=True)
        self.agent.translator = SimpleNamespace(translate_block=translate)

    async def begin(self):
        task = asyncio.create_task(self.agent.run_translation(self.doc_id, TranslateRequest(), LLMConfig()))
        try:
            await asyncio.wait_for(self.entered_second.wait(), 10)
        except BaseException:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        self.addAsyncCleanup(self.finish_task, task)
        return task

    async def finish_task(self, task):
        self.continue_second.set()
        await asyncio.gather(task, return_exceptions=True)

    async def test_page_is_readable_while_next_page_is_still_translating(self):
        task = await self.begin()
        meta = self.agent.get_meta(self.doc_id)
        self.assertEqual(meta.completed_pages, [1])
        self.assertEqual(meta.current_page, 2)
        self.assertFalse(meta.output_ready)
        self.assertFalse(task.done())
        with patch.object(documents, 'document_agent', self.agent):
            response = documents.preview_page(self.doc_id, 1, 'target', 1.0, meta.translation_run_id)
            self.assertTrue(response.body.startswith(b'\x89PNG'))
            state = await documents.get_document(self.doc_id)
            self.assertEqual([b.page for b in state['result'].blocks], [1])
            with self.assertRaises(HTTPException):
                documents.preview_page(self.doc_id, 2, 'target', 1.0)
            with self.assertRaises(HTTPException):
                await documents.download_translated(self.doc_id)
        self.continue_second.set()
        await task
        self.assertEqual(meta.completed_pages, [1, 2, 3])
        self.assertTrue(meta.output_ready)
        # The blank/protected page is also published, without a model request.
        with fitz.open(self.agent.output_pdf_path(self.doc_id)) as complete:
            self.assertEqual(len(complete), 3)
            for index in range(3):
                with fitz.open(self.agent.page_pdf_path(self.doc_id, index+1)) as single:
                    self.assertEqual(complete[index].get_pixmap().samples, single[0].get_pixmap().samples)

    async def test_failure_preserves_pages_and_retry_invalidates_old_generation(self):
        self.fail_second = True
        task = await self.begin()
        old_run = self.agent.get_meta(self.doc_id).translation_run_id
        self.continue_second.set()
        with self.assertRaises(RuntimeError):
            await task
        meta = self.agent.get_meta(self.doc_id)
        self.assertEqual(meta.status, 'failed')
        self.assertEqual(meta.completed_pages, [1])
        with patch('app.agents.orchestrator.get_settings', return_value=self.settings):
            recovered = DocumentAgent()
        self.assertEqual(recovered.get_meta(self.doc_id).completed_pages, [1])
        with patch.object(documents, 'document_agent', recovered):
            self.assertEqual(documents.preview_page(self.doc_id, 1, 'target', 1.0).status_code, 200)
        self.agent.begin_translation(self.doc_id, TranslateRequest(), LLMConfig())
        self.assertNotEqual(meta.translation_run_id, old_run)
        self.assertEqual(meta.completed_pages, [])
        self.assertEqual(self.agent.get_result(self.doc_id).blocks, [])
        with patch.object(documents, 'document_agent', self.agent):
            with self.assertRaises(HTTPException) as changed:
                documents.preview_page(self.doc_id, 1, 'target', 1.0, old_run)
            self.assertEqual(changed.exception.status_code, 409)
            with self.assertRaises(HTTPException):
                documents.preview_page(self.doc_id, 1, 'target', 1.0, meta.translation_run_id)

    async def test_layout_failure_does_not_publish_that_page(self):
        original = self.agent.composer.compose_page
        def compose(*args, **kwargs):
            if args[2] == 2:
                raise ValueError('layout rejected')
            return original(*args, **kwargs)
        self.agent.composer.compose_page = compose
        task = await self.begin()
        self.continue_second.set()
        with self.assertRaisesRegex(ValueError, 'layout rejected'):
            await task
        self.assertEqual(self.agent.get_meta(self.doc_id).completed_pages, [1])
        self.assertEqual([b.page for b in self.agent.get_result(self.doc_id).blocks], [1])
        self.assertFalse(self.agent.get_meta(self.doc_id).output_ready)


if __name__ == '__main__':
    unittest.main()
