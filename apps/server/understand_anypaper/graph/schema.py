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
    PROBLEM = "Problem"
    RESEARCH_GAP = "ResearchGap"
    CLAIM = "Claim"
    PRIOR_WORK = "PriorWork"
    DEFINITION = "Definition"
    OBSERVATION = "Observation"
    DESIGN_RATIONALE = "DesignRationale"
    CONCLUSION = "Conclusion"
    METHOD = "Method"
    MODULE = "Module"
    EQUATION = "Equation"
    ALGORITHM = "Algorithm"
    IMPLEMENTATION = "Implementation"
    TRAINING = "Training"
    INFERENCE = "Inference"
    DATASET = "Dataset"
    METRIC = "Metric"
    BASELINE = "Baseline"
    EXPERIMENT = "Experiment"
    ABLATION = "Ablation"
    RESULT = "Result"
    QUALITATIVE_RESULT = "QualitativeResult"
    EFFICIENCY = "Efficiency"
    EXTENSION = "Extension"
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
    """A typed argument or evidence node extracted from one paper."""

    id: str = Field(description="Identifier unique within the owning paper graph.")
    paper_id: str = Field(description="Identifier of the paper that owns this node.")
    node_type: NodeType = Field(description="Semantic role of the node in the argument graph.")
    title: str = Field(description="Short, concrete label displayed in the graph.")
    summary: str = Field(default="", description="Concise explanation of the node's content.")
    confidence: float = Field(
        ge=0,
        le=1,
        default=0.0,
        description="Confidence in the extracted node, from 0 to 1.",
    )
    source_type: str = Field(
        default="system_inferred",
        description="How the node was produced or grounded, such as pdf_block_span.",
    )
    semantic_unit_ids: list[str] = Field(
        default_factory=list,
        description="Semantic units in the current paper that ground this node.",
    )
    reference_ids: list[str] = Field(
        default_factory=list,
        description=(
            "PaperReference identifiers cited by this node. Select exact IDs from "
            "paper_references.json; do not invent identifiers."
        ),
    )
    page_ranges: list[tuple[int, int]] = Field(
        default_factory=list,
        description="Inclusive one-based page ranges containing the node's evidence.",
    )
    properties: dict[str, Any] = Field(
        default_factory=dict,
        description="Extensible node metadata, including the authoring source_location.",
    )
    created_by: str = Field(
        default="paper-graph-agent",
        description="Component or actor that created the node.",
    )
    verified: bool = Field(
        default=False,
        description="Whether a human has verified the node.",
    )


class GraphEdge(BaseModel):
    """A directed semantic relationship between two graph nodes."""

    id: str = Field(description="Identifier unique within the source node's paper graph.")
    source_paper_id: str = Field(
        description="Identifier of the source node's paper and the graph that owns this edge."
    )
    source_node_id: str = Field(description="Identifier of the edge's source node.")
    target_paper_id: str = Field(description="Identifier of the target node's paper.")
    target_node_id: str = Field(description="Identifier of the edge's target node.")
    edge_type: EdgeType = Field(description="Semantic relationship from source to target.")
    confidence: float = Field(
        ge=0,
        le=1,
        default=0.0,
        description="Confidence in the relationship, from 0 to 1.",
    )
    semantic_unit_ids: list[str] = Field(
        default_factory=list,
        description="Semantic units that provide evidence for this relationship.",
    )
    inference_type: str = Field(
        default="direct_extraction",
        description="Method used to infer the relationship.",
    )
    properties: dict[str, Any] = Field(
        default_factory=dict,
        description="Extensible relationship metadata.",
    )


class PaperArgumentGraph(BaseModel):
    """The complete, traceable argument graph for one paper."""

    paper_id: str = Field(description="Identifier of the paper represented by this graph.")
    summary: str = Field(
        description=(
            "A self-contained Markdown summary of the paper's motivation, approach, main results, "
            "and conclusions. Links to graph nodes use graph://<node-id>. This summarizes the "
            "paper as a whole rather than any single node."
        )
    )
    nodes: list[GraphNode] = Field(
        default_factory=list,
        description="All argument, structure, and evidence nodes in the graph.",
    )
    edges: list[GraphEdge] = Field(
        default_factory=list,
        description="All directed semantic relationships between graph nodes.",
    )
