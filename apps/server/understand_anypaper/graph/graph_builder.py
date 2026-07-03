from collections import defaultdict
import re
from uuid import uuid4

from understand_anypaper.analyzers.llm_analyzer import LLMAnalyzer
from understand_anypaper.graph.schema import EdgeType, EvidenceRef, GraphEdge, GraphNode, NodeType, PaperArgumentGraph
from understand_anypaper.parser.models import ContentBlock, ParsedPaper


_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it",
    "contribution", "main", "of", "on", "or", "our", "propose", "proposed", "that", "the",
    "their", "this", "to", "we", "with",
}
_ROLE_BASE_SCORE = {
    "gap": 0.48,
    "motivation": 0.46,
    "method": 0.5,
    "equation": 0.5,
    "figure": 0.44,
    "table": 0.44,
    "experiment": 0.5,
    "result": 0.52,
    "conclusion": 0.38,
    "background": 0.3,
}
_ROLE_FACET = {
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
}
_FACET_LIMITS = {"why": 3, "how": 5, "proof": 5, "context": 2}


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

        contribution_specs = self._contribution_specs(parsed)
        llm_links = self._analyzer.link_evidence(parsed, contribution_specs) if self._analyzer.available else None

        assignments: dict[str, set[str]] = defaultdict(set)
        for index, spec in enumerate(contribution_specs, start=1):
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
            self._attach_evidence_links(
                graph,
                parsed.blocks,
                contribution,
                anchor,
                assignments,
                llm_links.get(index) if llm_links else None,
            )

        self._attach_sequence_edges(graph, parsed.blocks)
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
                    return self._dedupe_contribution_specs(specs)

        contribution_blocks = [b for b in parsed.blocks if b.semantic_role == "contribution"]
        non_abstract_contributions = [
            block for block in contribution_blocks if not self._is_abstract_block(block)
        ]
        if non_abstract_contributions:
            contribution_blocks = non_abstract_contributions
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
        specs = self._dedupe_contribution_specs(specs)
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

    def _dedupe_contribution_specs(self, specs: list[dict]) -> list[dict]:
        merged: list[dict] = []
        for spec in specs:
            spec_terms = self._keywords(f"{spec['title']} {spec['summary']}")
            duplicate = None
            for existing in merged:
                existing_terms = self._keywords(f"{existing['title']} {existing['summary']}")
                if not spec_terms or not existing_terms:
                    continue
                overlap = len(spec_terms & existing_terms) / max(min(len(spec_terms), len(existing_terms)), 1)
                if overlap >= 0.45:
                    duplicate = existing
                    break
            if duplicate is None:
                merged.append(spec)
                continue
            known = {block.content_id for block in duplicate["evidence_blocks"]}
            duplicate["evidence_blocks"].extend(
                block for block in spec["evidence_blocks"] if block.content_id not in known
            )
            duplicate["confidence"] = max(duplicate["confidence"], spec["confidence"])
            if duplicate["title"].startswith("Contribution") and not spec["title"].startswith("Contribution"):
                duplicate["title"] = spec["title"]
        return merged

    def _attach_evidence_links(
        self,
        graph: PaperArgumentGraph,
        blocks: list[ContentBlock],
        contribution: GraphNode,
        anchor: ContentBlock,
        assignments: dict[str, set[str]],
        llm_links: list[dict] | None,
    ) -> None:
        selected = self._llm_selected_blocks(blocks, llm_links) if llm_links else []
        inference_type = "llm_evidence_link"
        if not selected:
            selected = self._rule_selected_blocks(blocks, contribution, anchor)
            inference_type = "role_keyword_link"

        for block, role, confidence, reason in selected:
            if block.content_id in contribution.evidence_ids:
                assignments[block.content_id].add(contribution.id)
                continue
            facet = self._facet_for_role(role)
            self._ensure_content_node(graph, block, role, facet)
            assignments[block.content_id].add(contribution.id)
            self._add_edge_once(
                graph,
                source_node_id=block.content_id,
                target_node_id=contribution.id,
                edge_type=self._edge_type_for_role(role),
                confidence=confidence,
                evidence=block.as_evidence(),
                inference_type=inference_type,
                properties={"semantic_role": role, "argument_facet": facet, "reason": reason},
            )

    def _llm_selected_blocks(
        self, blocks: list[ContentBlock], links: list[dict] | None
    ) -> list[tuple[ContentBlock, str, float, str]]:
        if not links:
            return []
        blocks_by_id = {block.content_id: block for block in blocks}
        selected: list[tuple[ContentBlock, str, float, str]] = []
        seen: set[str] = set()
        for link in sorted(links, key=lambda item: item.get("confidence", 0), reverse=True):
            content_id = link.get("content_id")
            block = blocks_by_id.get(content_id)
            if block is None or content_id in seen:
                continue
            role = str(link.get("role") or block.semantic_role)
            try:
                confidence = float(link.get("confidence", 0.75))
            except (TypeError, ValueError):
                confidence = 0.75
            selected.append(
                (
                    block,
                    role,
                    max(0.55, min(confidence, 0.95)),
                    str(link.get("reason") or ""),
                )
            )
            seen.add(content_id)
        return selected[:14]

    def _rule_selected_blocks(
        self, blocks: list[ContentBlock], contribution: GraphNode, anchor: ContentBlock
    ) -> list[tuple[ContentBlock, str, float, str]]:
        contribution_terms = self._keywords(f"{contribution.title} {contribution.summary}")
        candidates: list[tuple[float, ContentBlock, str, str]] = []
        for block in blocks:
            if block.content_id in contribution.evidence_ids or block.semantic_role == "contribution":
                continue
            score = self._evidence_score(block, anchor, contribution_terms)
            if score < 0.44:
                continue
            facet = self._facet_for_role(block.semantic_role)
            candidates.append((score, block, block.semantic_role, facet))

        selected: list[tuple[ContentBlock, str, float, str]] = []
        used: set[str] = set()
        for facet, limit in _FACET_LIMITS.items():
            facet_candidates = sorted(
                (item for item in candidates if item[3] == facet),
                key=lambda item: (item[0], -abs(item[1].order - anchor.order)),
                reverse=True,
            )
            for score, block, role, _ in facet_candidates[:limit]:
                if block.content_id in used:
                    continue
                used.add(block.content_id)
                selected.append((block, role, max(0.55, min(score, 0.92)), "role/proximity/keyword score"))

        if selected:
            return sorted(selected, key=lambda item: item[0].order)

        fallback = [
            block
            for block in blocks
            if block.content_id not in contribution.evidence_ids
            and block.semantic_role != "contribution"
            and abs(block.order - anchor.order) <= 3
        ]
        return [
            (block, block.semantic_role, 0.62, "near contribution statement")
            for block in fallback[:5]
        ]

    def _evidence_score(self, block: ContentBlock, anchor: ContentBlock, contribution_terms: set[str]) -> float:
        distance = abs(block.order - anchor.order)
        score = _ROLE_BASE_SCORE.get(block.semantic_role, 0.26)
        score += max(0.0, 1 - min(distance, 30) / 30) * 0.24
        if distance <= 2:
            score += 0.1
        if block.section and anchor.section and block.section == anchor.section:
            score += 0.12
        block_terms = self._keywords(block.text)
        if contribution_terms and block_terms:
            overlap = len(contribution_terms & block_terms) / max(len(contribution_terms), 3)
            score += min(overlap, 0.35) * 0.5
        if block.citations and anchor.citations and set(block.citations) & set(anchor.citations):
            score += 0.08
        if block.block_type in {"equation", "figure_caption", "table_caption"} and distance <= 8:
            score += 0.08
        return score

    def _ensure_content_node(self, graph: PaperArgumentGraph, block: ContentBlock, role: str, facet: str) -> None:
        existing = next((node for node in graph.nodes if node.id == block.content_id), None)
        if existing is not None:
            facets = set(existing.properties.get("argument_facets", []))
            facets.add(facet)
            existing.properties["argument_facets"] = sorted(facets)
            return
        graph.nodes.append(
            GraphNode(
                id=block.content_id,
                paper_id=graph.paper_id,
                node_type=self._node_type_for_role(role),
                title=self._title_for_block(block, role),
                summary=block.text[:700],
                confidence=0.72,
                source_type="content_atom",
                evidence_ids=[block.content_id],
                page_ranges=[(block.page, block.page)],
                properties={
                    "block_type": block.block_type,
                    "semantic_role": role,
                    "argument_facet": facet,
                    "argument_facets": [facet],
                    "order": block.order,
                    "section": block.section,
                },
                created_by="content-linker",
            )
        )

    def _attach_sequence_edges(self, graph: PaperArgumentGraph, blocks: list[ContentBlock]) -> None:
        node_ids = {node.id for node in graph.nodes}
        blocks_by_id = {block.content_id: block for block in blocks}
        for block in blocks:
            if block.content_id not in node_ids:
                continue
            for neighbor_id in block.neighbor_ids:
                neighbor = blocks_by_id.get(neighbor_id)
                if neighbor is None or neighbor.content_id not in node_ids or neighbor.order <= block.order:
                    continue
                self._add_edge_once(
                    graph,
                    source_node_id=block.content_id,
                    target_node_id=neighbor.content_id,
                    edge_type=EdgeType.NEXT,
                    confidence=0.7,
                    evidence=block.as_evidence(),
                    inference_type="document_order",
                    properties={"argument_facet": "context"},
                )

    def _add_edge_once(
        self,
        graph: PaperArgumentGraph,
        source_node_id: str,
        target_node_id: str,
        edge_type: EdgeType,
        confidence: float,
        evidence: EvidenceRef,
        inference_type: str,
        properties: dict,
    ) -> None:
        if any(
            edge.source_node_id == source_node_id
            and edge.target_node_id == target_node_id
            and edge.edge_type == edge_type
            for edge in graph.edges
        ):
            return
        graph.edges.append(
            GraphEdge(
                id=f"edge-{uuid4()}",
                paper_id=graph.paper_id,
                source_node_id=source_node_id,
                target_node_id=target_node_id,
                edge_type=edge_type,
                confidence=confidence,
                evidence=evidence,
                inference_type=inference_type,
                properties=properties,
            )
        )

    @staticmethod
    def _keywords(text: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", text.lower())
            if token not in _STOPWORDS
        }

    @staticmethod
    def _facet_for_role(role: str) -> str:
        return _ROLE_FACET.get(role, "context")

    @staticmethod
    def _title_for_block(block: ContentBlock, role: str) -> str:
        if block.heading:
            return block.heading
        sentence = re.split(r"(?<=[.!?])\s+", block.text.strip(), maxsplit=1)[0]
        compact = re.sub(r"\s+", " ", sentence).strip()
        if role == "equation":
            return "Equation"
        if role == "figure":
            return compact[:90] or "Figure"
        if role == "table":
            return compact[:90] or "Table"
        return compact[:90] or role.title()

    @staticmethod
    def _is_abstract_block(block: ContentBlock) -> bool:
        section = (block.section or "").strip().lower()
        text = block.text.strip().lower()
        return section.startswith("abstract") or text.startswith("abstract")

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
