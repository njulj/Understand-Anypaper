from typing import Any

from pydantic import BaseModel, Field

from understand_anypaper.graph.schema import EvidenceRef


class ContentBlock(BaseModel):
    content_id: str
    order: int
    page: int
    section: str | None = None
    heading: str | None = None
    bbox: list[float] | None = None
    text: str
    block_type: str = "paragraph"
    semantic_role: str = "background"
    citations: list[str] = Field(default_factory=list)
    neighbor_ids: list[str] = Field(default_factory=list)

    def as_evidence(self) -> EvidenceRef:
        return EvidenceRef(page=self.page, block_id=self.content_id, text=self.text, bbox=self.bbox)


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
    content_id: str
    sentence: str
    intent: str = "BACKGROUND"
    confidence: float = Field(ge=0, le=1, default=0.6)


class ParsedPaper(BaseModel):
    paper_id: str
    title: str
    abstract: str = ""
    blocks: list[ContentBlock] = Field(default_factory=list)
    references: list[PaperReference] = Field(default_factory=list)
    mentions: list[CitationMention] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
