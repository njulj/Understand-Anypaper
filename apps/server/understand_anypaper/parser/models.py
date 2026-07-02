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


class ParsedPaper(BaseModel):
    paper_id: str
    title: str
    abstract: str = ""
    blocks: list[ContentBlock] = Field(default_factory=list)
