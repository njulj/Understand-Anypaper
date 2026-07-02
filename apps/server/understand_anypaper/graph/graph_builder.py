from collections import defaultdict
from uuid import uuid4

from understand_anypaper.analyzers.llm_analyzer import LLMAnalyzer
from understand_anypaper.graph.schema import EdgeType, EvidenceRef, GraphEdge, GraphNode, NodeType, PaperArgumentGraph
from understand_anypaper.parser.models import ContentBlock, ParsedPaper


class PaperArgumentGraphBuilder:
    """Builds an evidence-backed PAG from parsed paper content.

    When an LLM is configured (PAG_OPENAI_API_KEY), semantic roles and
    contributions come from the LLM analyzer; otherwise the deterministic
    rule-based extraction is used. Both paths keep every node and edge
    traceable to content-block evidence.
    """

    def __init__(self, analyzer: LLMAnalyzer | None = None) -> None:
        self._analyzer = analyzer if analyzer is not None else LLMAnalyzer()

    def build(self, parsed: ParsedPaper) -> PaperArgumentGraph:
        self._apply_llm_roles(parsed)

        paper_node = GraphNode(
            id=f"paper-{parsed.paper_id}",
            paper_id=parsed.paper_id,
            node_type=NodeType.PAPER,
            title=parsed.title,
            summary=parsed.abstract,
            confidence=1.0,
            source_type="uploaded_document",
            evidence_ids=[block.content_id for block in parsed.blocks[:3]] or [parsed.paper_id],
            created_by="pdf-parser",
            verified=False,
        )
        graph = PaperArgumentGraph(paper_id=parsed.paper_id, nodes=[paper_node], edges=[])

        assignments: dict[str, set[str]] = defaultdict(set)
        for index, spec in enumerate(self._contribution_specs(parsed), start=1):
            contribution_id = f"contribution-{parsed.paper_id[:8]}-{index}"
            anchor = spec["evidence_blocks"][0]
            contribution = GraphNode(
                id=contribution_id,
                paper_id=parsed.paper_id,
                node_type=NodeType.CONTRIBUTION,
                title=spec["title"],
                summary=spec["summary"],
                confidence=spec["confidence"],
                source_type=spec["source_type"],
                evidence_ids=[block.content_id for block in spec["evidence_blocks"]],
                page_ranges=[(block.page, block.page) for block in spec["evidence_blocks"]],
                created_by=spec["created_by"],
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
                    evidence=anchor.as_evidence(),
                )
            )
            for block in spec["evidence_blocks"]:
                assignments[block.content_id].add(contribution_id)
            self._attach_neighborhood(graph, parsed.blocks, contribution, anchor, assignments)

        self._attach_references(graph, parsed, paper_node, assignments)
        return graph

    def _apply_llm_roles(self, parsed: ParsedPaper) -> None:
        roles = self._analyzer.classify_roles(parsed.blocks) if self._analyzer.available else None
        if not roles:
            return
        for block in parsed.blocks:
            if block.content_id in roles:
                block.semantic_role = roles[block.content_id]

    def _contribution_specs(self, parsed: ParsedPaper) -> list[dict]:
        blocks_by_id = {block.content_id: block for block in parsed.blocks}
        if self._analyzer.available:
            extracted = self._analyzer.extract_contributions(parsed)
            if extracted:
                specs = []
                for item in extracted:
                    evidence_blocks = [blocks_by_id[cid] for cid in item["evidence_content_ids"] if cid in blocks_by_id]
                    if not evidence_blocks:
                        continue
                    specs.append(
                        {
                            "title": item["title"],
                            "summary": item["summary"],
                            "evidence_blocks": evidence_blocks,
                            "confidence": 0.9,
                            "source_type": "llm_extracted",
                            "created_by": "llm-contribution-agent",
                        }
                    )
                if specs:
                    return specs

        contribution_blocks = [b for b in parsed.blocks if b.semantic_role == "contribution"]
        specs = [
            {
                "title": f"Contribution {index}",
                "summary": block.text,
                "evidence_blocks": [block],
                "confidence": 0.86,
                "source_type": "explicit" if "contribution" in block.text.lower() else "system_inferred",
                "created_by": "contribution-agent",
            }
            for index, block in enumerate(contribution_blocks, start=1)
        ]
        if not specs and parsed.blocks:
            # No explicit contribution cue anywhere: infer one from the abstract so
            # the graph still has an argument backbone to hang evidence on.
            anchor = parsed.blocks[0]
            specs = [
                {
                    "title": "Inferred contribution",
                    "summary": parsed.abstract or anchor.text[:500],
                    "evidence_blocks": [anchor],
                    "confidence": 0.4,
                    "source_type": "system_inferred",
                    "created_by": "contribution-agent",
                }
            ]
        return specs

    def _attach_neighborhood(
        self,
        graph: PaperArgumentGraph,
        blocks: list[ContentBlock],
        contribution: GraphNode,
        anchor: ContentBlock,
        assignments: dict[str, set[str]],
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
            assignments[block.content_id].add(contribution.id)
            graph.edges.append(
                GraphEdge(
                    id=f"edge-{uuid4()}",
                    paper_id=graph.paper_id,
                    source_node_id=block.content_id,
                    target_node_id=contribution.id,
                    edge_type=self._edge_type_for_role(block.semantic_role),
                    confidence=0.72,
                    evidence=block.as_evidence(),
                    inference_type="neighborhood_assignment",
                )
            )

    def _attach_references(
        self,
        graph: PaperArgumentGraph,
        parsed: ParsedPaper,
        paper_node: GraphNode,
        assignments: dict[str, set[str]],
    ) -> None:
        blocks_by_id = {block.content_id: block for block in parsed.blocks}
        mentions_by_reference: dict[str, list] = defaultdict(list)
        for mention in parsed.mentions:
            mentions_by_reference[mention.reference_id].append(mention)

        for reference in parsed.references:
            mentions = mentions_by_reference.get(reference.reference_id, [])
            node = GraphNode(
                id=reference.reference_id,
                paper_id=parsed.paper_id,
                node_type=NodeType.REFERENCE,
                title=reference.title or reference.raw_text[:80],
                summary=reference.raw_text,
                confidence=0.8,
                source_type="reference_entry",
                evidence_ids=[m.content_id for m in mentions] or [reference.reference_id],
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

            first_mention = mentions[0] if mentions else None
            citation_evidence = (
                EvidenceRef(
                    page=blocks_by_id[first_mention.content_id].page if first_mention.content_id in blocks_by_id else None,
                    block_id=first_mention.content_id,
                    text=first_mention.sentence,
                )
                if first_mention
                else EvidenceRef(text=reference.raw_text)
            )
            graph.edges.append(
                GraphEdge(
                    id=f"edge-{uuid4()}",
                    paper_id=parsed.paper_id,
                    source_node_id=paper_node.id,
                    target_node_id=reference.reference_id,
                    edge_type=EdgeType.CITES,
                    confidence=0.9,
                    evidence=citation_evidence,
                    properties={"mention_count": len(mentions)},
                )
            )

            linked: set[tuple[str, str]] = set()
            for mention in mentions:
                block = blocks_by_id.get(mention.content_id)
                for contribution_id in assignments.get(mention.content_id, set()):
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
                            evidence=EvidenceRef(
                                page=block.page if block else None,
                                block_id=mention.content_id,
                                text=mention.sentence,
                            ),
                            inference_type="citation_mention",
                            properties={"intent": mention.intent},
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
