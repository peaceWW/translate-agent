from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class BlockType(str, Enum):
    TITLE = "title"
    ABSTRACT = "abstract"
    SECTION = "section"
    PARAGRAPH = "paragraph"
    CAPTION = "caption"
    TABLE_TEXT = "table_text"
    FOOTNOTE = "footnote"
    FORMULA = "formula"
    FIGURE = "figure"
    TABLE = "table"
    HEADER = "header"
    FOOTER = "footer"
    PAGE_NUMBER = "page_number"
    REFERENCE = "reference"
    UNKNOWN = "unknown"


class BBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float


class LayoutBlock(BaseModel):
    source_id: str
    page: int
    type: BlockType
    bbox: BBox
    text: str = ""
    translate: bool = True
    protected: bool = False
    image_path: Optional[str] = None
    meta: dict[str, Any] = Field(default_factory=dict)


class TranslatedBlock(BaseModel):
    source_id: str
    page: int
    type: BlockType
    source_text: str
    translated_text: str
    translate: bool
    protected_hits: list[str] = Field(default_factory=list)
    qa: dict[str, Any] = Field(default_factory=dict)


class DocumentMeta(BaseModel):
    doc_id: str
    filename: str
    page_count: int
    status: str = "uploaded"
    progress: float = 0.0
    source_lang: str = "en"
    target_lang: str = "zh"
    created_at: str
    message: str = ""
    translation_model: Optional[str] = None
    translation_provider: Optional[str] = None
    output_ready: bool = False
    translation_run_id: Optional[str] = None
    completed_pages: list[int] = Field(default_factory=list)
    current_page: Optional[int] = None


class TranslateRequest(BaseModel):
    source_lang: str = "en"
    target_lang: str = "zh"


class LLMConfig(BaseModel):
    provider: str = "openai"
    model: str = "gpt-4o"
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    max_tokens: int = 4096
    system_prompt: str = (
        "你是一位专业的学术论文翻译助手。请准确翻译学术内容，"
        "严格保留公式、数字、单位、引用编号、图表编号与专有名词。"
    )


class QASummary(BaseModel):
    numeric_fidelity: bool = True
    unit_fidelity: bool = True
    formula_fidelity: bool = True
    citation_fidelity: bool = True
    missing_paragraphs: int = 0
    overall_pass: bool = True
    details: list[str] = Field(default_factory=list)


class TranslateResult(BaseModel):
    translation_run_id: Optional[str] = None
    doc_id: str
    status: str
    progress: float
    blocks: list[TranslatedBlock] = Field(default_factory=list)
    qa: QASummary = Field(default_factory=QASummary)
    layout_report: dict[str, Any] = Field(default_factory=dict)
    output_pdf: Optional[str] = None
    message: str = ""


class ChatRequest(BaseModel):
    doc_id: str
    question: str
    page: Optional[int] = None


class ChatResponse(BaseModel):
    answer: str
    citations: list[str] = Field(default_factory=list)
