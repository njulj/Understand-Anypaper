from enum import StrEnum
from typing import Any
from pydantic import BaseModel, Field


class NodeType(StrEnum):
    PAPER = "Paper"
    CONTRIBUTION = "Contribution"
    WHY = "Why"
    HOW = "How"
    PROOF = "Proof"
    MOTIVATION = "Motivation"
    RESEARCH_GAP = "ResearchGap"
    CLAIM = "Claim"
    CONCLUSION = "Conclusion"
    METHOD = "Method"
    MODULE = "Module"
    EQUATION = "Equation"
    ALGORITHM = "Algorithm"
    EXPERIMENT = "Experiment"
    RESULT = "Result"
    FIGURE = "Figure"
    TABLE = "Table"
    TEXT_BLOCK = "TextBlock"
    REFERENCE = "Reference"


class EdgeType(StrEnum):
    HAS_CONTRIBUTION = "HAS_CONTRIBUTION"
    HAS_MOTIVATION = "HAS_MOTIVATION"
    ADDRESSES = "ADDRESSES"
    IMPLEMENTED_BY = "IMPLEMENTED_BY"
    HAS_MODULE = "HAS_MODULE"
    SUPPORTED_BY = "SUPPORTED_BY"
    DESCRIBES = "DESCRIBES"
    EXPLAINS = "EXPLAINS"
    DEFINES = "DEFINES"
    ILLUSTRATES = "ILLUSTRATES"
    REPORTS = "REPORTS"
    PRODUCES = "PRODUCES"
    FORMALIZES = "FORMALIZES"
    JUSTIFIES = "JUSTIFIES"
    MOTIVATES = "MOTIVATES"
    VALIDATES = "VALIDATES"
    SUMMARIZES = "SUMMARIZES"
    CITES = "CITES"
    BUILDS_ON = "BUILDS_ON"
    EXTENDS = "EXTENDS"
    CONTRASTS_WITH = "CONTRASTS_WITH"
    NEXT = "NEXT"
    PREVIOUS = "PREVIOUS"
    CONTAINS = "CONTAINS"
    REFERENCED_BY = "REFERENCED_BY"


class GraphNode(BaseModel):
    id: str
    paper_id: str
    node_type: NodeType
    title: str
    summary: str = ""
    confidence: float = Field(ge=0, le=1, default=0.0)
    source_type: str = "system_inferred"
    semantic_unit_ids: list[str] = Field(default_factory=list)
    page_ranges: list[tuple[int, int]] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)
    created_by: str = "pag-builder"
    verified: bool = False


class GraphEdge(BaseModel):
    id: str
    paper_id: str
    source_node_id: str
    target_node_id: str
    edge_type: EdgeType
    confidence: float = Field(ge=0, le=1, default=0.0)
    semantic_unit_ids: list[str] = Field(default_factory=list)
    inference_type: str = "direct_extraction"
    properties: dict[str, Any] = Field(default_factory=dict)


class PaperArgumentGraph(BaseModel):
    paper_id: str
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
