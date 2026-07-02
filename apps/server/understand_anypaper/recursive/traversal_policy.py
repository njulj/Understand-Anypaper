from pydantic import BaseModel, Field


class TraversalPolicy(BaseModel):
    max_depth: int = Field(default=1, ge=0, le=2)
    max_papers: int = Field(default=5, ge=1, le=25)
    visited_paper_ids: set[str] = Field(default_factory=set)

    def can_expand(self, paper_id: str, depth: int) -> bool:
        if depth > self.max_depth:
            return False
        if len(self.visited_paper_ids) >= self.max_papers:
            return False
        return paper_id not in self.visited_paper_ids
