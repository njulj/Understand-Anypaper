# Understand Anypaper

Understand Anypaper turns research papers into an interactive **Paper Argument Graph (PAG)**: a directed, evidence-backed graph that connects each contribution with its motivation, research gap, method, equations, figures, experiments, results, conclusions, and references.

This repository contains an MVP scaffold for a client/server product inspired by Egonex-AI/Understand-Anything, but focused on traceable paper argument graphs rather than chapter summaries.

## MVP scope

- English, digital CS PDFs up to ~30 pages.
- Single-paper automatic analysis.
- Contribution-centric argument graph generation.
- Evidence and confidence stored on every generated node and edge.
- Content-level citation intent and on-demand reference recursion.
- Lightweight human correction via append-only graph patches.

## Architecture

```text
apps/
├── server/                 # FastAPI service; graph generation runs here
│   └── understand_anypaper/
│       ├── analyzers/      # Contribution extraction, role classification, citation intent
│       ├── api/            # REST API routers
│       ├── graph/          # PAG schema, builder, validation, SQL store
│       ├── parser/         # PDF/content atomization interfaces
│       ├── recursive/      # Reference recursion policy/cache
│       └── retrieval/      # Vector and graph retrieval interfaces
└── web/                    # React client shell
```

PostgreSQL with `pgvector` is the primary persistence target. The server schema stores graph nodes and edges as relational rows with JSONB properties and vector embeddings for semantic retrieval.

## Quick start

```bash
cp .env.example .env
docker compose up --build
```

Server API: <http://localhost:8000/docs>

Web client: <http://localhost:5173>

## API surface

- `POST /api/papers` uploads/registers a paper and queues analysis.
- `GET /api/papers/{paper_id}/graph` returns the global PAG.
- `GET /api/papers/{paper_id}/graph/subgraph?node_id=...&depth=2` returns a focused subgraph.
- `GET /api/nodes/{node_id}/evidence` returns source-backed evidence.
- `GET /api/content/{content_id}/assignments` explains contribution assignments.
- `POST /api/references/{reference_id}/resolve` resolves metadata for a citation.
- `POST /api/references/{reference_id}/analyze` recursively analyzes a cited paper within policy limits.
- `POST /api/graph/search` searches the graph with lexical/vector hooks.

## Development

```bash
cd apps/server
python -m venv .venv
. .venv/bin/activate
pip install -e .[dev]
pytest

cd ../web
npm install
npm run build
```

## Graph principles

1. **Contributions are first-class nodes.** The top-level experience starts from what the authors claim they contributed.
2. **Every node and edge is traceable.** Generated graph objects carry evidence IDs, page ranges, confidence, source type, and creator metadata.
3. **The graph is directed and shared.** Figures, equations, tables, experiments, and references can support multiple contributions.
4. **Human edits are patches.** Corrections are stored as operations instead of overwriting automatic extraction output.
5. **Reference recursion is bounded.** The default depth is 1, with max-paper and cycle-detection policies.
