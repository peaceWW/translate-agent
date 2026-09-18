from __future__ import annotations

import json
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

import aiofiles

from app.agents.translator import TranslationAgent
from app.agents.page_repair import PageRepairAgent
from app.core.config import get_settings
from app.models.schemas import (
    ChatRequest,
    ChatResponse,
    DocumentMeta,
    LayoutBlock,
    LLMConfig,
    TranslateRequest,
    TranslateResult,
    TranslatedBlock,
)
from app.services.llm import LLMService
from app.services.pdf_composer import PDFComposerService, LAYOUT_VERSION
from app.services.pdf_parser import PDFParserService
from app.services.qa import QAService
from app.services.structured_translator import StructuredTranslator


class DocumentAgent:
    """Orchestrates: parse → translate → QA → compose."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.llm = LLMService()
        self.parser = PDFParserService()
        self.composer = PDFComposerService()
        self.translator = TranslationAgent(self.llm)
        self.structured = StructuredTranslator(self.llm)
        self.page_repair = PageRepairAgent(self.llm)
        self.qa = QAService()
        self._docs: dict[str, DocumentMeta] = {}
        self._layouts: dict[str, list[LayoutBlock]] = {}
        self._results: dict[str, TranslateResult] = {}
        self._load_index()

    def _index_path(self) -> Path:
        return self.settings.data_dir / "documents.json"

    def _doc_dir(self, doc_id: str) -> Path:
        return self.settings.data_dir / "docs" / doc_id

    def _load_index(self) -> None:
        path = self._index_path()
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            dirty = False
            for item in raw:
                meta = DocumentMeta.model_validate(item)
                # Self-healing: if the doc directory was deleted (e.g. by delete_doc
                # but the index wasn't saved, or Windows file lock prevented rmtree),
                # skip it and mark the index for rewrite.
                doc_dir = self._doc_dir(meta.doc_id)
                if not doc_dir.exists():
                    dirty = True
                    continue
                if "output_ready" not in item:
                    meta.output_ready = meta.status == "completed" and self.output_pdf_path(meta.doc_id).exists()
                self._docs[meta.doc_id] = meta
            # Rewrite index if any stale entries were removed
            if dirty:
                self._save_index()

    def _save_index(self) -> None:
        path = self._index_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps([m.model_dump() for m in self._docs.values()], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def save_upload(self, filename: str, content: bytes) -> DocumentMeta:
        doc_id = uuid4().hex
        doc_dir = self._doc_dir(doc_id)
        doc_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = doc_dir / "source.pdf"
        async with aiofiles.open(pdf_path, "wb") as f:
            await f.write(content)

        page_count = self.parser.page_count(pdf_path)
        meta = DocumentMeta(
            doc_id=doc_id,
            filename=filename,
            page_count=page_count,
            status="uploaded",
            progress=0.0,
            created_at=datetime.now(timezone.utc).isoformat(),
            message="上传成功，待翻译",
        )
        self._docs[doc_id] = meta
        self._save_index()
        return meta

    def list_docs(self) -> list[DocumentMeta]:
        return sorted(self._docs.values(), key=lambda d: d.created_at, reverse=True)

    def get_meta(self, doc_id: str) -> Optional[DocumentMeta]:
        return self._docs.get(doc_id)

    def delete_doc(self, doc_id: str) -> None:
        """Delete a document and all associated files (source, translation, assets)."""
        if doc_id not in self._docs:
            raise ValueError("文档不存在")
        # Remove in-memory state first and persist immediately — this ensures
        # the index is updated even if directory deletion fails (Windows file locks).
        self._docs.pop(doc_id, None)
        self._results.pop(doc_id, None)
        self._layouts.pop(doc_id, None)
        self._save_index()
        # Remove on-disk data (after index is saved, so a crash here won't
        # cause the doc to reappear on restart — _load_index self-heals).
        doc_dir = self._doc_dir(doc_id)
        if doc_dir.exists():
            import shutil
            for _ in range(3):
                try:
                    shutil.rmtree(doc_dir)
                    break
                except Exception:  # noqa: BLE001
                    pass  # Windows file lock — retry

    def get_result(self, doc_id: str) -> Optional[TranslateResult]:
        if doc_id in self._results:
            return self._results[doc_id]
        result_path = self._doc_dir(doc_id) / "result.json"
        if result_path.exists():
            result = TranslateResult.model_validate(json.loads(result_path.read_text(encoding="utf-8")))
            self._results[doc_id] = result
            return result
        return None

    def get_layout(self, doc_id: str) -> list[LayoutBlock]:
        if doc_id in self._layouts:
            return self._layouts[doc_id]
        layout_path = self._doc_dir(doc_id) / "layout.json"
        if layout_path.exists():
            data = json.loads(layout_path.read_text(encoding="utf-8"))
            blocks = [LayoutBlock.model_validate(x) for x in data]
            self._layouts[doc_id] = blocks
            return blocks
        return []

    def source_pdf_path(self, doc_id: str) -> Path:
        return self._doc_dir(doc_id) / "source.pdf"

    def output_pdf_path(self, doc_id: str) -> Path:
        return self._doc_dir(doc_id) / "translated.pdf"

    def _save_result(self, result: TranslateResult) -> None:
        path = self._doc_dir(result.doc_id) / "result.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)
        self._results[result.doc_id] = result

    def page_pdf_path(self, doc_id: str, page_number: int) -> Path:
        meta = self._docs[doc_id]
        if not meta.translation_run_id:
            raise ValueError("该任务没有逐页结果")
        return self._doc_dir(doc_id) / "runs" / meta.translation_run_id / f"page-{page_number}.pdf"

    def begin_translation(self, doc_id: str, req: TranslateRequest, config: LLMConfig) -> str:
        meta = self._docs[doc_id]
        meta.translation_run_id = uuid4().hex
        meta.translation_model = config.model
        meta.translation_provider = config.provider
        meta.source_lang = req.source_lang
        meta.target_lang = req.target_lang
        meta.completed_pages = []
        meta.current_page = None
        meta.output_ready = False
        meta.status = "queued"
        meta.progress = 1
        meta.message = "已加入翻译队列"
        self._save_result(TranslateResult(
            doc_id=doc_id, translation_run_id=meta.translation_run_id,
            status="queued", progress=1, message=meta.message,
            layout_report={"version": LAYOUT_VERSION, "status": "in_progress"},
        ))
        self._save_index()
        return meta.translation_run_id

    def fail_translation(self, doc_id: str, message: str) -> None:
        meta = self._docs[doc_id]
        meta.status = "failed"
        meta.output_ready = False
        meta.message = f"{message}；已完成 {len(meta.completed_pages)}/{meta.page_count} 页仍可查看"
        result = self.get_result(doc_id)
        if result and result.translation_run_id == meta.translation_run_id:
            result.status = "failed"
            result.progress = meta.progress
            result.message = meta.message
            self._save_result(result)
        self._save_index()

    async def run_translation(self, doc_id: str, req: TranslateRequest,
                              config: LLMConfig | None = None, run_id: str | None = None) -> TranslateResult:
        if doc_id not in self._docs:
            raise ValueError("文档不存在")
        config = config or self.llm.load_config()
        run_id = run_id or self.begin_translation(doc_id, req, config)
        if self._docs[doc_id].translation_run_id != run_id:
            raise ValueError("翻译任务已更新")
        try:
            return await self._translate_pages(doc_id, req, config)
        except Exception as exc:
            self.fail_translation(doc_id, str(exc))
            raise

    async def _translate_pages(self, doc_id: str, req: TranslateRequest, config: LLMConfig) -> TranslateResult:
        meta = self._docs[doc_id]
        result = self.get_result(doc_id)
        assert result is not None
        meta.status = "parsing"
        meta.progress = 5
        meta.message = "正在解析 PDF 版面..."
        self._save_index()
        pdf_path = self.source_pdf_path(doc_id)
        blocks = await asyncio.to_thread(self.parser.parse, pdf_path, asset_dir=self._doc_dir(doc_id) / "assets")
        self._layouts[doc_id] = blocks
        (self._doc_dir(doc_id) / "layout.json").write_text(
            json.dumps([b.model_dump() for b in blocks], ensure_ascii=False, indent=2), encoding="utf-8",
        )
        if not any(b.translate and b.text.strip() for b in blocks):
            raise ValueError("未识别到可翻译文本，暂不支持扫描版 PDF")
        aggregate = dict(version=LAYOUT_VERSION, status="in_progress", translated_blocks=0,
                         retained_blocks=[], details=[], page_count=0, protected_regions_checked=0)
        # ── Page-level semantic units (Phase-2) with classic block fallback ──
        _TRANSLATE_WORKERS = 6
        _translate_sem = asyncio.Semaphore(_TRANSLATE_WORKERS)

        async def _classic_one(block: LayoutBlock) -> TranslatedBlock:
            async with _translate_sem:
                try:
                    return await self.translator.translate_block(
                        block, source_lang=req.source_lang, target_lang=req.target_lang,
                        config=config,
                    )
                except Exception as exc:
                    return TranslatedBlock(
                        source_id=block.source_id, page=block.page, type=block.type,
                        source_text=block.text,
                        translated_text=f'[翻译失败: {exc}]',
                        translate=True,
                    )

        for page_number in range(1, meta.page_count + 1):
            meta.current_page = page_number
            meta.status = "translating"
            page_blocks = [b for b in blocks if b.page == page_number]
            total = max(sum(b.translate for b in page_blocks), 1)
            meta.message = (
                f"正在语义翻译第 {page_number}/{meta.page_count} 页"
                f"（结构化 segments + {_TRANSLATE_WORKERS} 线程回退）"
            )
            self._save_index()
            page_results = await self.structured.translate_page_units(
                page_blocks,
                source_lang=req.source_lang,
                target_lang=req.target_lang,
                config=config,
                translate_block_fn=_classic_one,
            )
            failed = sum(
                1 for tb in page_results
                if tb.translated_text.startswith('[翻译失败:')
            )
            if failed:
                meta.message = (
                    f"第 {page_number}/{meta.page_count} 页：{failed} 段失败，正在页级补救…"
                )
                self._save_index()
                prev_blocks = [b for b in blocks if b.page == page_number - 1]
                next_blocks = [b for b in blocks if b.page == page_number + 1]
                page_results, repaired = await self.page_repair.repair_page(
                    page_blocks, page_results,
                    source_lang=req.source_lang, target_lang=req.target_lang,
                    config=config, prev_blocks=prev_blocks, next_blocks=next_blocks,
                )
                failed = sum(1 for tb in page_results if tb.translated_text.startswith('[翻译失败:'))
                if repaired:
                    aggregate.setdefault('page_repairs', 0)
                    aggregate['page_repairs'] += repaired
            absorbed = sum(1 for tb in page_results if tb.translated_text.startswith('[MERGED_INTO:'))
            if absorbed:
                aggregate.setdefault('semantic_merges', 0)
                aggregate['semantic_merges'] += absorbed
            meta.progress = 5 + 90 * ((page_number - 1) + .8) / meta.page_count
            if failed:
                meta.message = (
                    f"第 {page_number}/{meta.page_count} 页：已翻译 {total - failed}/{total} 段"
                    f"（{failed} 段仍失败，将保留原文）"
                )
            else:
                meta.message = f"第 {page_number}/{meta.page_count} 页：已翻译 {total}/{total} 段"
            self._save_index()
            meta.status = "composing"
            meta.message = f"正在校验并排版第 {page_number}/{meta.page_count} 页（Anchor/Flow）"
            self._save_index()
            report = {}
            # Native PDF work must not block the event loop serving progress requests.
            await asyncio.to_thread(self.composer.compose_page, pdf_path,
                                    self.page_pdf_path(doc_id, page_number), page_number,
                                    page_blocks, page_results, report=report)
            result.blocks.extend(page_results)
            result.qa = self.qa.check(result.blocks)
            for key in ("translated_blocks", "protected_regions_checked"):
                aggregate[key] += report.get(key, 0)
            if report.get('absorbed_blocks'):
                aggregate.setdefault('absorbed_blocks', 0)
                aggregate['absorbed_blocks'] += report['absorbed_blocks']
            for entry in report.get("retained_blocks", []):
                if entry.get("reason") == "没有有效译文，已保留原文":
                    entry["reason"] = "块级/页级翻译失败，已保留原文（非解析漏块）"
            aggregate["retained_blocks"].extend(report.get("retained_blocks", []))
            aggregate["details"] = [
                f"第 {entry['page']} 页：{entry['reason']}"
                for entry in aggregate["retained_blocks"]
            ]
            aggregate.setdefault("pixel_warnings", []).extend(report.get("pixel_warnings", []))
            if aggregate.get("pixel_warnings"):
                aggregate["details"].extend(aggregate["pixel_warnings"])
            aggregate["page_count"] = page_number
            aggregate["status"] = "needs_review" if aggregate["retained_blocks"] or aggregate["pixel_warnings"] else "in_progress"
            result.layout_report = dict(aggregate)
            meta.progress = 5 + 90 * page_number / meta.page_count
            meta.message = f"已完成 {page_number}/{meta.page_count} 页，可查看已完成页面"
            result.status = "translating"
            result.progress = meta.progress
            result.message = meta.message
            self._save_result(result)
            # Publish only after the checked PDF and its result are durably available.
            meta.completed_pages.append(page_number)
            self._save_index()
            await asyncio.sleep(0)

        meta.status = "composing"
        meta.current_page = None
        meta.message = "所有页面已可阅读，正在合并全文 PDF..."
        self._save_index()
        await asyncio.to_thread(self.composer.assemble_pages, pdf_path, self.output_pdf_path(doc_id),
                                [self.page_pdf_path(doc_id, p) for p in meta.completed_pages])
        result.layout_report["status"] = "needs_review" if aggregate["retained_blocks"] or aggregate.get("pixel_warnings") else "passed"
        if result.layout_report["status"] == "needs_review":
            result.qa.overall_pass = False
            result.qa.details.append("排版检查存在保留原文或保护区差异，请查看排版检查")
        result.status = "completed"
        result.progress = 100
        result.output_pdf = str(self.output_pdf_path(doc_id))
        result.message = "翻译完成" if result.qa.overall_pass else "翻译完成（存在 QA 告警）"
        self._save_result(result)
        meta.status = "completed"
        meta.output_ready = True
        meta.progress = 100
        meta.message = result.message
        self._save_index()
        return result

    def rebuild_layout(self, doc_id: str) -> TranslateResult:
        """Recompose cached translations without another model call."""
        meta = self.get_meta(doc_id)
        result = self.get_result(doc_id)
        if not meta or not result:
            raise ValueError("尚无可重排的译文")
        old = [b.model_copy(deep=True) for b in self.get_layout(doc_id)]
        fresh = self.parser.parse(self.source_pdf_path(doc_id), self._doc_dir(doc_id) / "assets")
        for block in old:
            # Normalization fixes can change text/tags while the source geometry
            # remains identical. Match that geometry so old jobs get the fixes too.
            candidates = [b for b in fresh if b.page == block.page and
                          max(abs(getattr(b.bbox, k) - getattr(block.bbox, k))
                              for k in ('x0', 'y0', 'x1', 'y1')) < 1]
            if candidates:
                match = min(candidates, key=lambda b: abs(b.bbox.x0-block.bbox.x0)+abs(b.bbox.y0-block.bbox.y0))
                block.bbox = match.bbox
                block.meta = match.meta
                block.type = match.type
                block.translate = match.translate
                block.protected = match.protected
        report = {}
        self.composer.compose(self.source_pdf_path(doc_id), self.output_pdf_path(doc_id), old, result.blocks, report=report)
        result.output_pdf = str(self.output_pdf_path(doc_id))
        result.layout_report = report
        result.message = "重排完成" if report['status'] == 'passed' else "重排完成（部分区域保留原文，请核对）"
        (self._doc_dir(doc_id) / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
        self._results[doc_id] = result
        meta.output_ready = True
        meta.message = result.message
        self._save_index()
        return result

    async def chat(self, req: ChatRequest) -> ChatResponse:
        result = self.get_result(req.doc_id)
        layout = self.get_layout(req.doc_id)
        context_parts: list[str] = []
        if result:
            for b in result.blocks:
                if req.page and b.page != req.page:
                    continue
                if b.translate and b.translated_text:
                    context_parts.append(f"[P{b.page}] {b.source_text}\n→ {b.translated_text}")
        elif layout:
            for b in layout:
                if req.page and b.page != req.page:
                    continue
                if b.text:
                    context_parts.append(f"[P{b.page}] {b.text}")

        context = "\n\n".join(context_parts[:40]) or "(暂无文档内容)"
        prompt = (
            f"基于以下论文片段回答用户问题。若不确定请说明。\n\n"
            f"文档内容：\n{context}\n\n问题：{req.question}"
        )
        answer = await self.llm.chat(prompt)
        citations = [p.split("\n", 1)[0] for p in context_parts[:5]]
        return ChatResponse(answer=answer, citations=citations)

    async def retranslate_block(
        self,
        doc_id: str,
        source_id: str,
        *,
        source_lang: str = "en",
        target_lang: str = "zh",
        manual_text: Optional[str] = None,
    ) -> Optional[TranslatedBlock]:
        result = self.get_result(doc_id)
        layout = self.get_layout(doc_id)
        if not result:
            return None
        block = next((b for b in layout if b.source_id == source_id), None)
        if not block:
            return None

        if manual_text is not None:
            tb = TranslatedBlock(
                source_id=source_id,
                page=block.page,
                type=block.type,
                source_text=block.text,
                translated_text=manual_text,
                translate=block.translate,
            )
        else:
            tb = await self.translator.translate_block(
                block,
                source_lang=source_lang,
                target_lang=target_lang,
            )

        new_blocks = [tb if b.source_id == source_id else b for b in result.blocks]
        qa = self.qa.check(new_blocks)
        result.blocks = new_blocks
        result.qa = qa
        (self._doc_dir(doc_id) / "result.json").write_text(
            result.model_dump_json(indent=2),
            encoding="utf-8",
        )
        # A modified block must invalidate the previous PDF on any layout failure.
        report = {}
        meta = self.get_meta(doc_id)
        try:
            self.composer.compose(self.source_pdf_path(doc_id), self.output_pdf_path(doc_id), layout, new_blocks, report=report)
            result.output_pdf = str(self.output_pdf_path(doc_id))
            if meta:
                meta.output_ready = True
        except Exception as exc:
            result.output_pdf = None
            report.update(version=LAYOUT_VERSION, status="failed", details=[str(exc)])
            result.qa.overall_pass = False
            if meta:
                meta.output_ready = False
        result.layout_report = report
        (self._doc_dir(doc_id) / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
        self._save_index()
        self._results[doc_id] = result
        return tb


# Singleton used by API
document_agent = DocumentAgent()
