"""Render PDF pages to PNG for vision-model translation."""
from __future__ import annotations

from pathlib import Path

import fitz

from app.services.pdf_runtime import serialized_pdf


class PageRenderer:
    """Rasterize PDF pages for multimodal translation inputs."""

    @serialized_pdf
    def render_page(
        self,
        pdf_path: Path,
        page_number: int,
        output_path: Path,
        *,
        scale: float = 2.0,
    ) -> Path:
        """Render a 1-based page to PNG and return the output path."""
        if scale <= 0:
            raise ValueError("scale 必须为正数")
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with fitz.open(pdf_path) as doc:
            if not 1 <= page_number <= len(doc):
                raise ValueError(f"页码不存在: {page_number}")
            page = doc[page_number - 1]
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            pix.save(str(output_path))
        return output_path

    @serialized_pdf
    def render_pages(
        self,
        pdf_path: Path,
        output_dir: Path,
        *,
        page_numbers: list[int] | None = None,
        scale: float = 2.0,
        name_template: str = "page-{page}.png",
    ) -> dict[int, Path]:
        """Render multiple pages; keys are 1-based page numbers."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        paths: dict[int, Path] = {}
        with fitz.open(pdf_path) as doc:
            indices = page_numbers or list(range(1, len(doc) + 1))
            for page_number in indices:
                if not 1 <= page_number <= len(doc):
                    raise ValueError(f"页码不存在: {page_number}")
                out = output_dir / name_template.format(page=page_number)
                page = doc[page_number - 1]
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                pix.save(str(out))
                paths[page_number] = out
        return paths
