import { GraphEdge, GraphNode, PaperArgumentGraph, PaperSummary, SemanticUnit } from './api';

export const MOCK_PAPER_ID = 'mock-paper-argument-graph';

function node(
  id: string,
  node_type: string,
  title: string,
  summary: string,
  unitIds: string[] = [],
): GraphNode {
  return {
    id,
    paper_id: MOCK_PAPER_ID,
    node_type,
    title,
    summary,
    confidence: 0.92,
    source_type: 'mock',
    semantic_unit_ids: unitIds,
    reference_ids: [],
    page_ranges: [[1, 1]],
    properties: { mock: true },
    created_by: 'mock',
    verified: false,
  };
}

function edge(id: string, source: string, target: string, edge_type: string, unitIds: string[] = []): GraphEdge {
  return {
    id,
    source_paper_id: MOCK_PAPER_ID,
    source_node_id: source,
    target_paper_id: MOCK_PAPER_ID,
    target_node_id: target,
    edge_type,
    confidence: 0.9,
    semantic_unit_ids: unitIds,
    inference_type: 'mock',
    properties: { mock: true },
  };
}

function unit(id: string, role: string, title: string, text: string): SemanticUnit {
  return {
    semantic_unit_id: id,
    paper_id: MOCK_PAPER_ID,
    role,
    title,
    text,
    source_location: {
      page: 1,
      bbox: [],
      extracted_text: text,
      block_id: `mock-block-${id}`,
      start_offset: 0,
      end_offset: text.length,
      extraction_method: 'mock',
      segments: [],
    },
    confidence: 1,
    created_by: 'mock',
    properties: { mock: true },
  };
}

export const mockPaper: PaperSummary = {
  paper_id: MOCK_PAPER_ID,
  title: 'Mock Paper: Evidence-Grounded Learning Systems',
  abstract: 'A representative Paper Argument Graph used to develop and review the reading experience.',
  metadata: { mock: true },
};

export const mockSemanticUnits: SemanticUnit[] = [
  unit('mock-u-motivation', 'motivation', 'Reader burden', 'Readers must repeatedly connect a paper’s claims, evidence, and prior work across distant sections.'),
  unit('mock-u-gap', 'research_gap', 'Missing structure', 'Existing PDF readers expose text and annotations but do not make an author’s argument structure inspectable.'),
  unit('mock-u-method', 'method', 'Argument graph', 'We represent a paper as a contribution-centered graph linking motivation, method, formalization, and validation.'),
  unit('mock-u-equation', 'equation', 'Coverage score', 'Coverage is the fraction of contribution facets that have at least one grounded evidence node.'),
  unit('mock-u-experiment', 'experiment', 'User study', 'Twenty-four readers completed comprehension tasks with either a PDF reader or the graph workspace.'),
  unit('mock-u-result', 'result', 'Comprehension result', 'Graph workspace users identified supporting evidence more accurately and with less navigation.'),
  unit('mock-u-figure', 'figure', 'Graph overview', 'The overview places contributions at the center and recursively expands their evidence subgraphs.'),
  unit('mock-u-conclusion', 'conclusion', 'Takeaway', 'Argument graphs can turn a linear paper into a traceable structure for learning and review.'),
];

export const mockGraph: PaperArgumentGraph = {
  paper_id: MOCK_PAPER_ID,
  summary: 'This mock paper demonstrates a **traceable Paper Argument Graph**. Start with [Contribution 1](graph://mock-contribution-1), then inspect the linked motivation, method, equation, experiment, and result.',
  nodes: [
    node('mock-paper', 'Paper', mockPaper.title, mockPaper.abstract),
    node('mock-contribution-1', 'Contribution', 'Contribution 1: Make paper arguments navigable', 'A contribution-centered graph connects the question, implementation, evidence, and prior work behind each claim.', ['mock-u-method']),
    node('mock-contribution-2', 'Contribution', 'Contribution 2: Measure evidence coverage', 'A coverage score reveals whether a contribution has grounded motivation, method, and validation evidence.', ['mock-u-equation']),
    node('mock-why-1', 'Why', 'Why this is needed', 'The reader burden and a missing argument structure motivate the contribution.'),
    node('mock-how-1', 'How', 'How the graph is built', 'The paper is decomposed into connected semantic nodes and evidence relations.'),
    node('mock-proof-1', 'Proof', 'Proof from evaluation', 'A user study validates the navigation benefit.'),
    node('mock-motivation-1', 'Motivation', 'Readers lose the argument across sections', 'Readers must reconstruct how claims and evidence fit together.', ['mock-u-motivation']),
    node('mock-gap-1', 'ResearchGap', 'PDF readers do not expose argument structure', 'Text and annotations alone do not provide a navigable rationale.', ['mock-u-gap']),
    node('mock-method-1', 'Method', 'Contribution-centered evidence graph', 'Build a graph around each contribution and recursively attach support.', ['mock-u-method']),
    node('mock-equation-1', 'Equation', 'Evidence coverage score', 'Quantify grounded facets per contribution.', ['mock-u-equation']),
    node('mock-experiment-1', 'Experiment', 'Comparative reader study', 'Compare comprehension with graph-assisted and ordinary PDF reading.', ['mock-u-experiment']),
    node('mock-result-1', 'Result', 'Readers find evidence faster', 'The graph workspace improves evidence identification and reduces navigation.', ['mock-u-result']),
    node('mock-figure-1', 'Figure', 'Contribution evidence overview', 'A visual overview of the expandable argument graph.', ['mock-u-figure']),
    node('mock-conclusion-1', 'Conclusion', 'Traceable structure improves learning', 'The argument graph is a useful layer above the original paper.', ['mock-u-conclusion']),
    node('mock-reference-1', 'Reference', 'Prior work on structured scholarly reading', 'This contribution builds on prior interfaces for scholarly navigation.'),
  ],
  edges: [
    edge('mock-e-paper-c1', 'mock-paper', 'mock-contribution-1', 'CONTAINS'),
    edge('mock-e-paper-c2', 'mock-paper', 'mock-contribution-2', 'CONTAINS'),
    edge('mock-e-c1-why', 'mock-contribution-1', 'mock-why-1', 'CONTAINS'),
    edge('mock-e-c1-how', 'mock-contribution-1', 'mock-how-1', 'CONTAINS'),
    edge('mock-e-c1-proof', 'mock-contribution-1', 'mock-proof-1', 'CONTAINS'),
    edge('mock-e-why-motivation', 'mock-why-1', 'mock-motivation-1', 'MOTIVATES', ['mock-u-motivation']),
    edge('mock-e-why-gap', 'mock-why-1', 'mock-gap-1', 'SUPPORTED_BY', ['mock-u-gap']),
    edge('mock-e-how-method', 'mock-how-1', 'mock-method-1', 'IMPLEMENTED_BY', ['mock-u-method']),
    edge('mock-e-how-equation', 'mock-how-1', 'mock-equation-1', 'FORMALIZES', ['mock-u-equation']),
    edge('mock-e-proof-experiment', 'mock-proof-1', 'mock-experiment-1', 'VALIDATES', ['mock-u-experiment']),
    edge('mock-e-proof-result', 'mock-proof-1', 'mock-result-1', 'REPORTS', ['mock-u-result']),
    edge('mock-e-c1-figure', 'mock-contribution-1', 'mock-figure-1', 'ILLUSTRATES', ['mock-u-figure']),
    edge('mock-e-c1-reference', 'mock-contribution-1', 'mock-reference-1', 'BUILDS_ON'),
    edge('mock-e-c2-equation', 'mock-contribution-2', 'mock-equation-1', 'FORMALIZES', ['mock-u-equation']),
    edge('mock-e-paper-conclusion', 'mock-paper', 'mock-conclusion-1', 'SUMMARIZES', ['mock-u-conclusion']),
  ],
};
