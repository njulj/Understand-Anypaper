from dataclasses import dataclass

from understand_anypaper.graph.schema import NodeType, PaperArgumentGraph


@dataclass(frozen=True)
class CompletenessScore:
    contribution_id: str
    motivation: float
    method: float
    equations: float
    experimental_evidence: float
    references: float

    @property
    def overall(self) -> float:
        return round((self.motivation + self.method + self.equations + self.experimental_evidence + self.references) / 5, 2)


class GraphValidator:
    def score_completeness(self, graph: PaperArgumentGraph) -> list[CompletenessScore]:
        scores: list[CompletenessScore] = []
        by_id = {node.id: node for node in graph.nodes}
        for contribution in [n for n in graph.nodes if n.node_type == NodeType.CONTRIBUTION]:
            facet_ids = {
                edge.target_node_id
                for edge in graph.edges
                if edge.source_node_id == contribution.id and edge.target_node_id in by_id
            }
            neighbors = [
                by_id[edge.target_node_id]
                for edge in graph.edges
                if edge.source_node_id in facet_ids and edge.target_node_id in by_id
            ]
            neighbors.extend(
                by_id[edge.source_node_id]
                for edge in graph.edges
                if edge.target_node_id == contribution.id and edge.source_node_id in by_id
            )
            types = {node.node_type for node in neighbors}
            scores.append(
                CompletenessScore(
                    contribution_id=contribution.id,
                    motivation=1.0 if NodeType.MOTIVATION in types or NodeType.RESEARCH_GAP in types else 0.0,
                    method=1.0 if NodeType.METHOD in types or NodeType.MODULE in types else 0.0,
                    equations=1.0 if NodeType.EQUATION in types else 0.0,
                    experimental_evidence=1.0 if NodeType.EXPERIMENT in types or NodeType.RESULT in types else 0.0,
                    references=1.0 if NodeType.REFERENCE in types else 0.0,
                )
            )
        return scores
