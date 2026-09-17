from __future__ import annotations

from typing import Optional, Literal
from functools import lru_cache
import fitz

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app.agents.orchestrator import document_agent
from app.services.pdf_runtime import serialized_pdf
from app.services.pdf_composer import LAYOUT_VERSION
from app.models.schemas import ChatRequest, TranslateRequest

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.post("/upload")
async def upload_document(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "仅支持 PDF 文件")
    content = await file.read()
    if len(content) > 100 * 1024 * 1024:
        raise HTTPException(400, "文件不能超过 100MB")
    meta = await document_agent.save_upload(file.filename, content)
    return meta


@router.get("")
async def list_documents():
    return document_agent.list_docs()


@router.get("/{doc_id}")
async def get_document(doc_id: str):
    meta = document_agent.get_meta(doc_id)
    if not meta:
        raise HTTPException(404, "文档不存在")
    result = document_agent.get_result(doc_id)
    return {"meta": meta, "result": result}


@router.delete("/{doc_id}")
async def delete_document(doc_id: str):
    meta = document_agent.get_meta(doc_id)
    if not meta:
        raise HTTPException(404, "文档不存在")
    if meta.status in {"queued", "parsing", "translating", "qa", "composing"}:
        raise HTTPException(409, "该文档正在翻译，无法删除")
    document_agent.delete_doc(doc_id)
    return {"ok": True, "doc_id": doc_id}


@router.post("/{doc_id}/translate")
async def translate_document(doc_id: str, req: TranslateRequest, background_tasks: BackgroundTasks):
    meta = document_agent.get_meta(doc_id)
    if not meta:
        raise HTTPException(404, "文档不存在")

    if meta.status in {"queued", "parsing", "translating", "qa", "composing"}:
        raise HTTPException(409, "该文档正在翻译，请等待完成")
    config = document_agent.llm.load_config().model_copy(deep=True)

    run_id = document_agent.begin_translation(doc_id, req, config)

    async def _run() -> None:
        try:
            await document_agent.run_translation(doc_id, req, config, run_id)
        except Exception:
            # The agent records failure while preserving already-published pages.
            pass

    background_tasks.add_task(_run)
    return meta


@router.get("/{doc_id}/layout")
async def get_layout(doc_id: str):
    if not document_agent.get_meta(doc_id):
        raise HTTPException(404, "文档不存在")
    return document_agent.get_layout(doc_id)


@router.get("/{doc_id}/result")
async def get_result(doc_id: str):
    meta = document_agent.get_meta(doc_id)
    if not meta:
        raise HTTPException(404, "文档不存在")
    result = document_agent.get_result(doc_id)
    if not result:
        return {"status": meta.status, "progress": meta.progress, "message": meta.message}
    return result


@router.get("/{doc_id}/source.pdf")
async def download_source(doc_id: str):
    path = document_agent.source_pdf_path(doc_id)
    if not path.exists():
        raise HTTPException(404, "源文件不存在")
    return FileResponse(path, media_type="application/pdf", filename="source.pdf")


@router.get("/{doc_id}/translated.pdf")
async def download_translated(doc_id: str):
    meta = document_agent.get_meta(doc_id)
    if not meta or meta.status != "completed" or not meta.output_ready:
        raise HTTPException(404, "当前任务的译文 PDF 尚未生成")
    path = document_agent.output_pdf_path(doc_id)
    if not path.exists():
        raise HTTPException(404, "译文 PDF 尚未生成")
    return FileResponse(path, media_type="application/pdf", filename="translated.pdf")


class RetranslateBody(BaseModel):
    manual_text: Optional[str] = None
    source_lang: str = "en"
    target_lang: str = "zh"


@router.post("/{doc_id}/blocks/{source_id}/retranslate")
async def retranslate_block(doc_id: str, source_id: str, body: RetranslateBody):
    tb = await document_agent.retranslate_block(
        doc_id,
        source_id,
        source_lang=body.source_lang,
        target_lang=body.target_lang,
        manual_text=body.manual_text,
    )
    if not tb:
        raise HTTPException(404, "块不存在或尚未翻译")
    return tb


@router.post("/chat")
async def chat(req: ChatRequest):
    if not document_agent.get_meta(req.doc_id):
        raise HTTPException(404, "文档不存在")
    return await document_agent.chat(req)


@router.post("/{doc_id}/rebuild-layout")
def rebuild_layout(doc_id: str):
    meta = document_agent.get_meta(doc_id)
    if not meta:
        raise HTTPException(404, "文档不存在")
    if meta.status != "completed":
        raise HTTPException(409, "请等待翻译完成后再重排")
    meta.status = "composing"
    meta.completed_pages = []
    meta.output_ready = False
    document_agent._save_index()
    try:
        return document_agent.rebuild_layout(doc_id)
    except Exception as exc:
        result = document_agent.get_result(doc_id)
        if result:
            result.output_pdf = None
            result.layout_report = {"version": LAYOUT_VERSION, "status": "failed", "details": [str(exc)]}
            (document_agent._doc_dir(doc_id) / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
        meta.message = f"重排失败：{exc}"
        raise HTTPException(422, f"重排未通过检查：{exc}") from exc
    finally:
        meta.status = "completed"
        document_agent._save_index()


@lru_cache(maxsize=24)
@serialized_pdf
def _render_page(path: str, modified: int, page_number: int, scale: float) -> bytes:
    with fitz.open(path) as doc:
        if page_number < 1 or page_number > len(doc):
            raise HTTPException(404, "页码不存在")
        return doc[page_number - 1].get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png")


@router.get("/{doc_id}/pages/{page_number}.png")
def preview_page(doc_id: str, page_number: int, kind: Literal["source", "target"] = "source",
                 scale: float = Query(default=1.6, ge=1, le=3), run_id: str | None = None):
    meta = document_agent.get_meta(doc_id)
    if not meta:
        raise HTTPException(404, "文档不存在")
    if page_number < 1 or page_number > meta.page_count:
        raise HTTPException(404, "页码不存在")
    render_number = page_number
    if kind == "source":
        path = document_agent.source_pdf_path(doc_id)
    else:
        if run_id and run_id != meta.translation_run_id:
            raise HTTPException(409, "翻译任务已更新，请刷新页面")
        if meta.status == "completed" and meta.output_ready:
            path = document_agent.output_pdf_path(doc_id)
        elif meta.translation_run_id and page_number in meta.completed_pages:
            path = document_agent.page_pdf_path(doc_id, page_number)
            render_number = 1
        else:
            raise HTTPException(404, "该页译文尚未生成")
    if not path.exists():
        raise HTTPException(404, "PDF 不存在")
    content = _render_page(str(path), path.stat().st_mtime_ns, render_number, scale)
    return Response(content, media_type="image/png", headers={"Cache-Control": "no-cache"})
