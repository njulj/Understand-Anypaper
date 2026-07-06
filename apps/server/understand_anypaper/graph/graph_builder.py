from collections import defaultdict
from uuid import uuid4

from understand_anypaper.graph.schema import EdgeType, GraphEdge, GraphNode, NodeType, PaperArgumentGraph
from understand_anypaper.parser.models import ParsedPaper, SemanticUnit


class GraphBuildError(RuntimeError):
    pass


class PaperArgumentGraphBuilder:
    """Builds a PAG from LLM-sliced semantic units.

    Semantic roles and page/bbox evidence belong to SemanticUnit objects produced
    by the LLM analyzer.
    """

    def build(self, parsed: ParsedPaper) -> PaperArgumentGraph:
        semantic_units = self._semantic_units(parsed)
        parsed.semantic_units = semantic_units

        paper_node = GraphNode(
            id=f"paper-{parsed.paper_id}",
            paper_id=parsed.paper_id,
            node_type=NodeType.PAPER,
            title=parsed.title,
            summary=parsed.abstract,
            confidence=1.0,
            source_type="uploaded_document",
            semantic_unit_ids=[unit.semantic_unit_id for unit in semantic_units[:3]],
            page_ranges=self._page_ranges_for_units(semantic_units[:3]),
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
        facet_ids: dict[str, dict[str, str]] = {}
        for index, unit in enumerate(contributions, start=1):
            contribution_id = f"contribution-{parsed.paper_id[:8]}-{index}"
            contribution_ids[unit.semantic_unit_id] = contribution_id
            facet_ids[contribution_id] = {}
            graph.nodes.append(
                self._node_from_unit(
                    unit,
                    contribution_id,
                    NodeType.CONTRIBUTION,
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
            for facet in ("why", "how", "proof"):
                facet_id = self._facet_node_id(contribution_id, facet)
                facet_ids[contribution_id][facet] = facet_id
                graph.nodes.append(
                    GraphNode(
                        id=facet_id,
                        paper_id=parsed.paper_id,
                        node_type=self._node_type_for_facet(facet),
                        title=self._facet_title(facet),
                        summary=self._facet_summary(facet),
                        confidence=unit.confidence,
                        source_type="system_inferred",
                        semantic_unit_ids=[unit.semantic_unit_id],
                        page_ranges=self._page_ranges_for_units([unit]),
                        properties={"argument_facet": facet, "parent_contribution_id": contribution_id},
                        created_by="pag-builder",
                    )
                )
                graph.edges.append(
                    GraphEdge(
                        id=f"edge-{uuid4()}",
                        paper_id=parsed.paper_id,
                        source_node_id=contribution_id,
                        target_node_id=facet_id,
                        edge_type=EdgeType.CONTAINS,
                        confidence=unit.confidence,
                        semantic_unit_ids=[unit.semantic_unit_id],
                        inference_type="argument_facet",
                        properties={"argument_facet": facet},
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
                    source_type="semantic_unit",
                    created_by=unit.created_by,
                )
            )
            facet = self._facet_for_role(unit.role)
            for contribution_id in self._target_contributions(unit, contribution_ids):
                facet_id = facet_ids[contribution_id][facet]
                graph.edges.append(
                    GraphEdge(
                        id=f"edge-{uuid4()}",
                        paper_id=parsed.paper_id,
                        source_node_id=facet_id,
                        target_node_id=node_id,
                        edge_type=self._edge_type_for_role(unit.role),
                        confidence=unit.confidence,
                        semantic_unit_ids=[unit.semantic_unit_id],
                        inference_type="llm_semantic_unit",
                        properties={"semantic_role": unit.role, "argument_facet": facet},
                    )
                )

        return graph

    def _semantic_units(self, parsed: ParsedPaper) -> list[SemanticUnit]:
        if parsed.semantic_units:
            return parsed.semantic_units
        raise GraphBuildError("Semantic units are required to build a Paper Argument Graph")

    def _node_from_unit(
        self,
        unit: SemanticUnit,
        node_id: str,
        node_type: NodeType,
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
            page_ranges=self._page_ranges_for_units([unit]),
            properties={
                "semantic_role": unit.role,
                "evidence": [evidence.model_dump() for evidence in unit.evidence],
                **unit.properties,
            },
            created_by=created_by,
        )

    def _target_contributions(
        self,
        unit: SemanticUnit,
        contribution_ids: dict[str, str],
    ) -> list[str]:
        explicit_ids = unit.properties.get("contribution_unit_ids")
        if not isinstance(explicit_ids, list):
            raise GraphBuildError(
                f"Semantic unit {unit.semantic_unit_id} is missing LLM contribution assignment"
            )
        targets = [
            contribution_ids[item]
            for item in explicit_ids
            if isinstance(item, str) and item in contribution_ids
        ]
        if len(targets) != len([item for item in explicit_ids if isinstance(item, str)]):
            raise GraphBuildError(
                f"Semantic unit {unit.semantic_unit_id} references an unknown contribution"
            )
        return targets

    def _attach_sequence_edges(
        self,
        graph: PaperArgumentGraph,
        semantic_units: list[SemanticUnit],
    ) -> None:
        ordered_units = sorted(
            semantic_units,
            key=lambda unit: min(
                ((evidence.page, evidence.bbox[0], evidence.bbox[1]) for evidence in unit.evidence),
                default=(10**9, 1.0, 1.0),
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

    @staticmethod
    def _page_ranges_for_units(
        units: list[SemanticUnit],
    ) -> list[tuple[int, int]]:
        pages = sorted({
            evidence.page
            for unit in units
            for evidence in unit.evidence
        })
        return [(page, page) for page in pages]

    @staticmethod
    def _facet_for_role(role: str) -> str:
        return {
            "gap": "why",
            "motivation": "why",
            "background": "why",
            "reference": "why",
            "method": "how",
            "equation": "how",
            "figure": "how",
            "table": "proof",
            "experiment": "proof",
            "result": "proof",
            "conclusion": "proof",
        }.get(role, "why")

    @staticmethod
    def _facet_for_intent(intent: str) -> str:
        return {
            "USES_METHOD": "how",
            "SUPPORTS_CLAIM": "proof",
        }.get(intent, "why")

    @staticmethod
    def _facet_node_id(contribution_id: str, facet: str) -> str:
        return f"{contribution_id}-{facet}"

    @staticmethod
    def _node_type_for_facet(facet: str) -> NodeType:
        return {
            "why": NodeType.WHY,
            "how": NodeType.HOW,
            "proof": NodeType.PROOF,
        }[facet]

    @staticmethod
    def _facet_title(facet: str) -> str:
        return {
            "why": "WHY / 为什么",
            "how": "HOW / 怎么做",
            "proof": "PROOF / 如何证明",
        }[facet]

    @staticmethod
    def _facet_summary(facet: str) -> str:
        return {
            "why": "Motivation, research gaps, limitations, prior work, and references.",
            "how": "Methods, modules, equations, figures, and algorithms.",
            "proof": "Experiments, tables, results, evidence, and conclusions.",
        }[facet]

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
