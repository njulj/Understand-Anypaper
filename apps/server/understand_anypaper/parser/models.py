from typing import Any

from pydantic import BaseModel, Field


class SourceBlock(BaseModel):
    source_block_id: str
    order: int
    page: int
    section: str | None = None
    heading: str | None = None
    bbox: list[float] | None = None
    text: str
    block_type: str = "paragraph"
    citations: list[str] = Field(default_factory=list)
    neighbor_ids: list[str] = Field(default_factory=list)


class SourceRange(BaseModel):
    source_block_id: str
    start_char: int | None = None
    end_char: int | None = None


class SemanticUnit(BaseModel):
    semantic_unit_id: str
    paper_id: str
    role: str
    title: str
    text: str
    source_ranges: list[SourceRange]
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


class CitationMention(BaseModel):
    mention_id: str
    reference_id: str
    source_block_id: str
    sentence: str
    intent: str = "BACKGROUND"
    confidence: float = Field(ge=0, le=1, default=0.6)


class ParsedPaper(BaseModel):
    paper_id: str
    title: str
    abstract: str = ""
    source_blocks: list[SourceBlock] = Field(default_factory=list)
    semantic_units: list[SemanticUnit] = Field(default_factory=list)
    references: list[PaperReference] = Field(default_factory=list)
    mentions: list[CitationMention] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
