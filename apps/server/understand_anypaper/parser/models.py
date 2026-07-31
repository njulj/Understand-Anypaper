from typing import Any, Literal

from pydantic import BaseModel, Field


class DocumentPage(BaseModel):
    page: int
    width: float
    height: float
    image_width: int | None = None
    image_height: int | None = None
    image_mime_type: str = "image/png"
    image_data: bytes = Field(default=b"", exclude=True)


class SourceBlockSpan(BaseModel):
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    bbox: list[float] = Field(
        min_length=4,
        max_length=4,
        description="Normalized [ymin, xmin, ymax, xmax] coordinates.",
    )


class SourceBlock(BaseModel):
    block_id: str
    page: int = Field(ge=1)
    kind: Literal["text", "image"] = "text"
    text: str = ""
    bbox: list[float] = Field(
        min_length=4,
        max_length=4,
        description="Normalized [ymin, xmin, ymax, xmax] coordinates.",
    )
    spans: list[SourceBlockSpan] = Field(default_factory=list, exclude=True)


class PageSourceSegment(BaseModel):
    page: int
    bbox: list[float] = Field(
        min_length=4,
        max_length=4,
        description="Normalized [ymin, xmin, ymax, xmax] coordinates on the rendered page.",
    )
    extracted_text: str = ""
    block_id: str = ""
    start_offset: int = Field(default=0, ge=0)
    end_offset: int = Field(default=0, ge=0)
    extraction_method: str = "block_offset"


class PageSourceLocation(PageSourceSegment):
    segments: list[PageSourceSegment] = Field(default_factory=list)


class SemanticUnit(BaseModel):
    semantic_unit_id: str
    paper_id: str
    role: str
    title: str
    text: str
    source_location: PageSourceLocation
    confidence: float = Field(ge=0, le=1, default=0.0)
    created_by: str = "paper-graph-agent"
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
    source_blocks: list[SourceBlock] = Field(default_factory=list, exclude=True)
    semantic_units: list[SemanticUnit] = Field(default_factory=list)
    references: list[PaperReference] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_bytes: bytes = Field(default=b"", exclude=True)
    source_media_type: str = ""
