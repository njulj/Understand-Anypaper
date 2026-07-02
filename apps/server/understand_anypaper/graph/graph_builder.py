from uuid import uuid4

from understand_anypaper.graph.schema import EdgeType, GraphEdge, GraphNode, NodeType, PaperArgumentGraph
from understand_anypaper.parser.models import ContentBlock, ParsedPaper


class PaperArgumentGraphBuilder:
    """Builds an evidence-backed PAG from parsed paper content.

    The MVP implementation is deterministic and conservative: it creates explicit
    contribution nodes only when the parser/analyzer found contribution cues, then
    links nearby content blocks as traceable evidence. LLM/Codex powered analyzers
    can replace the stub extraction components behind the same contract.
    """

    def build(self, parsed: ParsedPaper) -> PaperArgumentGraph:
        paper_node = GraphNode(
            id=f"paper-{parsed.paper_id}",
            paper_id=parsed.paper_id,
            node_type=NodeType.PAPER,
            title=parsed.title,
            summary=parsed.abstract,
            confidence=1.0,
            source_type="uploaded_document",
            evidence_ids=[block.content_id for block in parsed.blocks[:3]],
            created_by="pdf-parser",
            verified=False,
        )
        graph = PaperArgumentGraph(paper_id=parsed.paper_id, nodes=[paper_node], edges=[])

        contribution_blocks = [b for b in parsed.blocks if b.semantic_role == "contribution"]
        for index, block in enumerate(contribution_blocks, start=1):
            contribution_id = f"contribution-{index}"
            contribution = GraphNode(
                id=contribution_id,
                paper_id=parsed.paper_id,
                node_type=NodeType.CONTRIBUTION,
                title=f"Contribution {index}",
                summary=block.text,
                confidence=0.86,
                source_type="explicit" if "contribution" in block.text.lower() else "system_inferred",
                evidence_ids=[block.content_id],
                page_ranges=[(block.page, block.page)],
                created_by="contribution-agent",
            )
            graph.nodes.append(contribution)
            graph.edges.append(
                GraphEdge(
                    id=f"edge-{uuid4()}",
                    paper_id=parsed.paper_id,
                    source_node_id=paper_node.id,
                    target_node_id=contribution_id,
                    edge_type=EdgeType.HAS_CONTRIBUTION,
                    confidence=0.9,
                    evidence=block.as_evidence(),
                )
            )
            self._attach_neighborhood(graph, parsed.blocks, contribution, block)
        return graph

    def _attach_neighborhood(
        self,
        graph: PaperArgumentGraph,
        blocks: list[ContentBlock],
        contribution: GraphNode,
        anchor: ContentBlock,
    ) -> None:
        for block in blocks:
            if abs(block.order - anchor.order) > 2 or block.content_id == anchor.content_id:
                continue
            node_type = self._node_type_for_role(block.semantic_role)
            node = GraphNode(
                id=block.content_id,
                paper_id=graph.paper_id,
                node_type=node_type,
                title=block.heading or block.semantic_role.title(),
                summary=block.text[:500],
                confidence=0.7,
                source_type="content_atom",
                evidence_ids=[block.content_id],
                page_ranges=[(block.page, block.page)],
                properties={"block_type": block.block_type, "semantic_role": block.semantic_role},
                created_by="content-assigner",
            )
            if node.id not in {existing.id for existing in graph.nodes}:
                graph.nodes.append(node)
            graph.edges.append(
                GraphEdge(
                    id=f"edge-{uuid4()}",
                    paper_id=graph.paper_id,
                    source_node_id=block.content_id,
                    target_node_id=contribution.id,
                    edge_type=EdgeType.DESCRIBES,
                    confidence=0.72,
                    evidence=block.as_evidence(),
                    inference_type="neighborhood_assignment",
                )
            )

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
        }.get(role, NodeType.TEXT_BLOCK)
