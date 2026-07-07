from dataclasses import dataclass

from understand_anypaper.graph.schema import NodeType, PaperArgumentGraph


MOTIVATION_TYPES = {
    NodeType.MOTIVATION,
    NodeType.PROBLEM,
    NodeType.RESEARCH_GAP,
    NodeType.PRIOR_WORK,
    NodeType.OBSERVATION,
    NodeType.DESIGN_RATIONALE,
}
METHOD_TYPES = {
    NodeType.METHOD,
    NodeType.MODULE,
    NodeType.ALGORITHM,
    NodeType.IMPLEMENTATION,
    NodeType.TRAINING,
    NodeType.INFERENCE,
    NodeType.EXTENSION,
}
EXPERIMENTAL_EVIDENCE_TYPES = {
    NodeType.EXPERIMENT,
    NodeType.DATASET,
    NodeType.METRIC,
    NodeType.BASELINE,
    NodeType.ABLATION,
    NodeType.RESULT,
    NodeType.QUALITATIVE_RESULT,
    NodeType.EFFICIENCY,
    NodeType.TABLE,
}


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
                    motivation=1.0 if types & MOTIVATION_TYPES else 0.0,
                    method=1.0 if types & METHOD_TYPES else 0.0,
                    equations=1.0 if NodeType.EQUATION in types else 0.0,
                    experimental_evidence=1.0 if types & EXPERIMENTAL_EVIDENCE_TYPES else 0.0,
                    references=1.0 if NodeType.REFERENCE in types else 0.0,
                )
            )
        return scores
