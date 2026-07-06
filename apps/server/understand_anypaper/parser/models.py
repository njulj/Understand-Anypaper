from typing import Any

from pydantic import BaseModel, Field


class DocumentPage(BaseModel):
    page: int
    width: float
    height: float
    image_width: int | None = None
    image_height: int | None = None
    image_mime_type: str = "image/png"
    image_data: bytes = Field(default=b"", exclude=True)


class PageSourceLocation(BaseModel):
    page: int
    bbox: list[float] = Field(
        min_length=4,
        max_length=4,
        description="Normalized [ymin, xmin, ymax, xmax] coordinates on the rendered page.",
    )
    extracted_text: str = ""
    start_text: str = ""
    end_text: str = ""
    extraction_method: str = "pymupdf_clip"


class SemanticUnit(BaseModel):
    semantic_unit_id: str
    paper_id: str
    role: str
    title: str
    text: str
    source_location: PageSourceLocation
    confidence: float = Field(ge=0, le=1, default=0.0)
    created_by: str = "llm-semantic-slicer"
    properties: dict[str, Any] = Field(default_factory=dict)


class PaperReference(BaseModel):
    reference_id: str
    marker: str | None = None
    raw_text: str
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    doi: str | None = None
    arxiv_id: str | None = None


class ParsedPaper(BaseModel):
    paper_id: str
    title: str
    abstract: str = ""
    pages: list[DocumentPage] = Field(default_factory=list)
    semantic_units: list[SemanticUnit] = Field(default_factory=list)
    references: list[PaperReference] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_bytes: bytes = Field(default=b"", exclude=True)
    source_media_type: str = ""
