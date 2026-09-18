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
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    api_key: str = ""
    base_url: str = "https://api.deepseek.com/v1"
    max_tokens: int = 4096
    system_prompt: str = (
        "你是一位专业的学术论文翻译助手。请准确翻译学术内容，"
        "严格保留公式、数字、单位、引用编号、图表编号与专有名词。"
    )
    # Dedicated translation model — faster, translation-optimized.
    # Falls back to the main model if not configured.
    translation_model: str = ""          # e.g. "qwen-mt-plus"
    translation_base_url: str = ""       # e.g. "https://dashscope.aliyuncs.com/compatible-mode/v1"
    translation_api_key: str = ""        # separate API key if needed
    # Vision / multimodal model for page-level visual translation.
    # Falls back to the main model if not configured.
    vision_model: str = ""               # e.g. "qwen-vl-max", "gpt-4o"
    vision_base_url: str = ""
    vision_api_key: str = ""
    vision_scale: float = 2.0            # page render DPI multiplier

    def effective_translation_config(self) -> "LLMConfig":
        """Return a config suitable for translation calls.

        If a dedicated translation model is configured, return a copy
        pointing to it; otherwise fall back to the main model.
        """
        if self.translation_model:
            return self.model_copy(update={
                "provider": f"translation-{self.provider}",
                "model": self.translation_model,
                "base_url": self.translation_base_url or self.base_url,
                "api_key": self.translation_api_key or self.api_key,
            })
        return self

    def effective_vision_config(self) -> "LLMConfig":
        """Return a config suitable for multimodal / vision translation calls."""
        if self.vision_model:
            return self.model_copy(update={
                "provider": f"vision-{self.provider}",
                "model": self.vision_model,
                "base_url": self.vision_base_url or self.base_url,
                "api_key": self.vision_api_key or self.api_key,
            })
        return self

    def has_vision_model(self) -> bool:
        """True when a dedicated vision model is configured or the main model looks multimodal."""
        name = (self.vision_model or self.model or "").lower()
        markers = ("vl", "vision", "gpt-4o", "gpt-4.1", "gemini", "claude-3", "claude-4", "qwen2.5-vl")
        return bool(self.vision_model) or any(m in name for m in markers)


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
