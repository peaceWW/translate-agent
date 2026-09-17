import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from app.models.schemas import LLMConfig, LayoutBlock, BBox, BlockType, TranslatedBlock
from app.services.llm import LLMService
from app.agents.translator import TranslationAgent, ACADEMIC_POLICY
from app.services.qa import QAService

class TranslationReliabilityTests(unittest.TestCase):
    def test_legacy_temperature_is_ignored(self):
        cfg = LLMConfig.model_validate({'temperature': 0.9})
        self.assertNotIn('temperature', cfg.model_dump())

    def test_fixed_sampling_and_snapshot(self):
        service = LLMService()
        create = AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='译文'))]))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with patch.object(service, '_client', return_value=client):
            result = asyncio.run(service.chat('text', config=LLMConfig(model='snapshot-model'), temperature=1))
        self.assertEqual(result, '译文')
        self.assertEqual(create.call_args.kwargs['temperature'], 0)
        self.assertEqual(create.call_args.kwargs['model'], 'snapshot-model')

    def test_incomplete_and_empty_responses_fail(self):
        for finish, content in [('length', 'partial'), ('stop', ''), ('content_filter', None)]:
            service = LLMService()
            create = AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=content))]))
            with patch.object(service, '_client', return_value=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))):
                with self.assertRaises(ValueError):
                    asyncio.run(service.chat('text', config=LLMConfig()))

    def test_failed_translation_is_not_source_fallback(self):
        llm = SimpleNamespace(chat=AsyncMock(side_effect=RuntimeError('unavailable')))
        agent = TranslationAgent(llm)
        block = LayoutBlock(source_id='p1', page=1, type=BlockType.PARAGRAPH, bbox=BBox(x0=0,y0=0,x1=10,y1=10), text='A result')
        with self.assertRaises(RuntimeError):
            asyncio.run(agent.translate_block(block, source_lang='en', target_lang='zh', config=LLMConfig()))
        self.assertIn(ACADEMIC_POLICY, llm.chat.call_args.kwargs['system_prompt'])

    def test_old_failure_markers_and_empty_translations_fail_qa(self):
        def block(text):
            return TranslatedBlock(source_id='p1', page=1, type=BlockType.PARAGRAPH, source_text='laser', translated_text=text, translate=True)
        self.assertFalse(QAService().check([block('[翻译失败: test] laser')]).overall_pass)
        self.assertEqual(QAService().check([block('')]).missing_paragraphs, 1)

if __name__ == '__main__':
    unittest.main()
