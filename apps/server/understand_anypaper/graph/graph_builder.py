from collections import defaultdict
from uuid import uuid4

from understand_anypaper.analyzers.llm_analyzer import LLMAnalyzer
from understand_anypaper.graph.schema import EdgeType, GraphEdge, GraphNode, NodeType, PaperArgumentGraph
from understand_anypaper.parser.models import ParsedPaper, SemanticUnit, SourceBlock


class GraphBuildError(RuntimeError):
    pass


class PaperArgumentGraphBuilder:
    """Builds a PAG from LLM-sliced semantic units.

    The parser only provides source blocks for location and citation extraction.
    Semantic roles belong to SemanticUnit objects produced by the LLM analyzer.
    """

    def __init__(self, analyzer: LLMAnalyzer | None = None) -> None:
        self._analyzer = analyzer if analyzer is not None else LLMAnalyzer()

    def build(self, parsed: ParsedPaper) -> PaperArgumentGraph:
        semantic_units = self._semantic_units(parsed)
        parsed.semantic_units = semantic_units

        source_blocks = {block.source_block_id: block for block in parsed.source_blocks}
        paper_node = GraphNode(
            id=f"paper-{parsed.paper_id}",
            paper_id=parsed.paper_id,
            node_type=NodeType.PAPER,
            title=parsed.title,
            summary=parsed.abstract,
            confidence=1.0,
            source_type="uploaded_document",
            semantic_unit_ids=[unit.semantic_unit_id for unit in semantic_units[:3]],
            page_ranges=self._page_ranges_for_units(semantic_units[:3], source_blocks),
            created_by="pdf-parser",
            verified=False,
        )
        graph = PaperArgumentGraph(paper_id=parsed.paper_id, nodes=[paper_node], edges=[])

        units_by_role: dict[str, list[SemanticUnit]] = defaultdict(list)
        for unit in semantic_units:
            units_by_role[unit.role].append(unit)

        contributions = units_by_role.get("contribution", [])
        if not contributions:
            raise GraphBuildError("LLM semantic slicing produced no contribution units")

        contribution_ids: dict[str, str] = {}
        source_block_assignments: dict[str, set[str]] = defaultdict(set)
        for index, unit in enumerate(contributions, start=1):
            contribution_id = f"contribution-{parsed.paper_id[:8]}-{index}"
            contribution_ids[unit.semantic_unit_id] = contribution_id
            for source_block_id in self._unit_source_block_ids(unit):
                source_block_assignments[source_block_id].add(contribution_id)
            graph.nodes.append(
                self._node_from_unit(
                    unit,
                    contribution_id,
                    NodeType.CONTRIBUTION,
                    source_blocks,
                    source_type="llm_extracted",
                    created_by="llm-contribution-agent",
                )
            )
            graph.edges.append(
                GraphEdge(
                    id=f"edge-{uuid4()}",
                    paper_id=parsed.paper_id,
                    source_node_id=paper_node.id,
                    target_node_id=contribution_id,
                    edge_type=EdgeType.HAS_CONTRIBUTION,
                    confidence=unit.confidence,
                    semantic_unit_ids=[unit.semantic_unit_id],
                    inference_type="llm_semantic_unit",
                )
            )

        evidence_units = [unit for unit in semantic_units if unit.role != "contribution"]
        for unit in evidence_units:
            node_id = unit.semantic_unit_id
            graph.nodes.append(
                self._node_from_unit(
                    unit,
                    node_id,
                    self._node_type_for_role(unit.role),
                    source_blocks,
                    source_type="semantic_unit",
                    created_by=unit.created_by,
                )
            )
            for contribution_id in self._target_contributions(unit, contributions, contribution_ids):
                for source_block_id in self._unit_source_block_ids(unit):
                    source_block_assignments[source_block_id].add(contribution_id)
                graph.edges.append(
                    GraphEdge(
                        id=f"edge-{uuid4()}",
                        paper_id=parsed.paper_id,
                        source_node_id=node_id,
                        target_node_id=contribution_id,
                        edge_type=self._edge_type_for_role(unit.role),
                        confidence=unit.confidence,
                        semantic_unit_ids=[unit.semantic_unit_id],
                        inference_type="llm_semantic_unit",
                        properties={"semantic_role": unit.role, "argument_facet": self._facet_for_role(unit.role)},
                    )
                )

        self._attach_sequence_edges(graph, semantic_units, source_blocks)
        self._attach_references(graph, parsed, paper_node, semantic_units, source_block_assignments)
        return graph

    def _semantic_units(self, parsed: ParsedPaper) -> list[SemanticUnit]:
        if parsed.semantic_units:
            return parsed.semantic_units
        units = self._analyzer.slice_semantic_units(parsed) if self._analyzer.available else None
        if not units:
            raise GraphBuildError("LLM semantic slicing is required to build a Paper Argument Graph")
        return units

    def _node_from_unit(
        self,
        unit: SemanticUnit,
        node_id: str,
        node_type: NodeType,
        source_blocks: dict[str, SourceBlock],
        source_type: str,
        created_by: str,
    ) -> GraphNode:
        return GraphNode(
            id=node_id,
            paper_id=unit.paper_id,
            node_type=node_type,
            title=unit.title,
            summary=unit.text,
            confidence=unit.confidence,
            source_type=source_type,
            semantic_unit_ids=[unit.semantic_unit_id],
            page_ranges=self._page_ranges_for_units([unit], source_blocks),
            properties={
                "semantic_role": unit.role,
                "source_ranges": [source_range.model_dump() for source_range in unit.source_ranges],
                **unit.properties,
            },
            created_by=created_by,
        )

    def _target_contributions(
        self,
        unit: SemanticUnit,
        contributions: list[SemanticUnit],
        contribution_ids: dict[str, str],
    ) -> list[str]:
        explicit_ids = unit.properties.get("contribution_unit_ids")
        if isinstance(explicit_ids, list):
            targets = [
                contribution_ids[item]
                for item in explicit_ids
                if isinstance(item, str) and item in contribution_ids
            ]
            if targets:
                return targets

        unit_blocks = self._unit_source_block_ids(unit)
        overlapping = [
            contribution_ids[contribution.semantic_unit_id]
            for contribution in contributions
            if unit_blocks & self._unit_source_block_ids(contribution)
        ]
        if overlapping:
            return overlapping

        return [
            contribution_ids[min(
                contributions,
                key=lambda contribution: abs(self._first_source_order(unit) - self._first_source_order(contribution)),
            ).semantic_unit_id]
        ]

    def _attach_sequence_edges(
        self,
        graph: PaperArgumentGraph,
        semantic_units: list[SemanticUnit],
        source_blocks: dict[str, SourceBlock],
    ) -> None:
        order_by_block = {block_id: block.order for block_id, block in source_blocks.items()}
        ordered_units = sorted(
            semantic_units,
            key=lambda unit: min(
                (order_by_block.get(source_range.source_block_id, 10**9) for source_range in unit.source_ranges),
                default=10**9,
            ),
        )
        node_ids = {node.id for node in graph.nodes}
        for current, following in zip(ordered_units, ordered_units[1:]):
            source_id = self._node_id_for_unit(graph, current)
            target_id = self._node_id_for_unit(graph, following)
            if not source_id or not target_id or source_id == target_id:
                continue
            if source_id not in node_ids or target_id not in node_ids:
                continue
            graph.edges.append(
                GraphEdge(
                    id=f"edge-{uuid4()}",
                    paper_id=graph.paper_id,
                    source_node_id=source_id,
                    target_node_id=target_id,
                    edge_type=EdgeType.NEXT,
                    confidence=0.7,
                    semantic_unit_ids=[current.semantic_unit_id, following.semantic_unit_id],
                    inference_type="document_order",
                    properties={"argument_facet": "context"},
                )
            )

    @staticmethod
    def _node_id_for_unit(graph: PaperArgumentGraph, unit: SemanticUnit) -> str | None:
        for node in graph.nodes:
            if unit.semantic_unit_id in node.semantic_unit_ids:
                return node.id
        return None

    def _attach_references(
        self,
        graph: PaperArgumentGraph,
        parsed: ParsedPaper,
        paper_node: GraphNode,
        semantic_units: list[SemanticUnit],
        source_block_assignments: dict[str, set[str]],
    ) -> None:
        unit_ids_by_source_block: dict[str, list[str]] = defaultdict(list)
        for unit in semantic_units:
            for source_block_id in self._unit_source_block_ids(unit):
                unit_ids_by_source_block[source_block_id].append(unit.semantic_unit_id)

        mentions_by_reference: dict[str, list] = defaultdict(list)
        for mention in parsed.mentions:
            mentions_by_reference[mention.reference_id].append(mention)

        for reference in parsed.references:
            mentions = mentions_by_reference.get(reference.reference_id, [])
            semantic_unit_ids = sorted({
                unit_id
                for mention in mentions
                for unit_id in unit_ids_by_source_block.get(mention.source_block_id, [])
            })
            node = GraphNode(
                id=reference.reference_id,
                paper_id=parsed.paper_id,
                node_type=NodeType.REFERENCE,
                title=reference.title or reference.raw_text[:80],
                summary=reference.raw_text,
                confidence=0.8,
                source_type="reference_entry",
                semantic_unit_ids=semantic_unit_ids,
                properties={
                    "marker": reference.marker,
                    "authors": reference.authors,
                    "year": reference.year,
                    "doi": reference.doi,
                    "arxiv_id": reference.arxiv_id,
                },
                created_by="reference-extractor",
            )
            graph.nodes.append(node)
            graph.edges.append(
                GraphEdge(
                    id=f"edge-{uuid4()}",
                    paper_id=parsed.paper_id,
                    source_node_id=paper_node.id,
                    target_node_id=reference.reference_id,
                    edge_type=EdgeType.CITES,
                    confidence=0.9,
                    semantic_unit_ids=semantic_unit_ids,
                    properties={"mention_count": len(mentions)},
                )
            )

            linked: set[tuple[str, str]] = set()
            for mention in mentions:
                for contribution_id in source_block_assignments.get(mention.source_block_id, set()):
                    edge_type = self._edge_type_for_intent(mention.intent)
                    if (contribution_id, edge_type) in linked:
                        continue
                    linked.add((contribution_id, edge_type))
                    graph.edges.append(
                        GraphEdge(
                            id=f"edge-{uuid4()}",
                            paper_id=parsed.paper_id,
                            source_node_id=reference.reference_id,
                            target_node_id=contribution_id,
                            edge_type=edge_type,
                            confidence=mention.confidence,
                            semantic_unit_ids=unit_ids_by_source_block.get(mention.source_block_id, []),
                            inference_type="citation_mention",
                            properties={"intent": mention.intent, "source_block_id": mention.source_block_id},
                        )
                    )

    @staticmethod
    def _unit_source_block_ids(unit: SemanticUnit) -> set[str]:
        return {source_range.source_block_id for source_range in unit.source_ranges}

    @staticmethod
    def _first_source_order(unit: SemanticUnit) -> int:
        for source_range in unit.source_ranges:
            tail = source_range.source_block_id.rsplit("block", 1)[-1]
            if tail.isdigit():
                return int(tail)
        return 10**9

    @staticmethod
    def _page_ranges_for_units(
        units: list[SemanticUnit],
        source_blocks: dict[str, SourceBlock],
    ) -> list[tuple[int, int]]:
        pages = sorted({
            source_blocks[source_range.source_block_id].page
            for unit in units
            for source_range in unit.source_ranges
            if source_range.source_block_id in source_blocks
        })
        return [(page, page) for page in pages]

    @staticmethod
    def _facet_for_role(role: str) -> str:
        return {
            "gap": "why",
            "motivation": "why",
            "background": "context",
            "method": "how",
            "equation": "how",
            "figure": "how",
            "table": "proof",
            "experiment": "proof",
            "result": "proof",
            "conclusion": "proof",
        }.get(role, "context")

    @staticmethod
    def _node_type_for_role(role: str) -> NodeType:
        return {
            "motivation": NodeType.MOTIVATION,
            "gap": NodeType.RESEARCH_GAP,
            "method": NodeType.METHOD,
            "experiment": NodeType.EXPERIMENT,
            "result": NodeType.RESULT,
            "conclusion": NodeType.CONCLUSION,
            "reference": NodeType.REFERENCE,
            "equation": NodeType.EQUATION,
            "figure": NodeType.FIGURE,
            "table": NodeType.TABLE,
        }.get(role, NodeType.TEXT_BLOCK)

    @staticmethod
    def _edge_type_for_role(role: str) -> EdgeType:
        return {
            "motivation": EdgeType.MOTIVATES,
            "gap": EdgeType.MOTIVATES,
            "method": EdgeType.IMPLEMENTED_BY,
            "experiment": EdgeType.VALIDATES,
            "result": EdgeType.SUPPORTED_BY,
            "conclusion": EdgeType.SUMMARIZES,
            "equation": EdgeType.FORMALIZES,
            "figure": EdgeType.ILLUSTRATES,
            "table": EdgeType.REPORTS,
        }.get(role, EdgeType.DESCRIBES)

    @staticmethod
    def _edge_type_for_intent(intent: str) -> EdgeType:
        return {
            "EXTENDS": EdgeType.EXTENDS,
            "COMPARES_WITH": EdgeType.CONTRASTS_WITH,
            "CONTRADICTS": EdgeType.CONTRASTS_WITH,
            "IDENTIFIES_LIMITATION": EdgeType.MOTIVATES,
            "SUPPORTS_CLAIM": EdgeType.SUPPORTED_BY,
        }.get(intent, EdgeType.BUILDS_ON)
