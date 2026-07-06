# Understand Anypaper

Understand Anypaper turns research papers into an interactive **Paper Argument Graph (PAG)**: a directed, evidence-backed graph that connects each contribution with its motivation, research gap, method, equations, figures, experiments, results, conclusions, and references.
<img width="4528" height="2652" alt="image" src="https://github.com/user-attachments/assets/16a0857e-476f-4338-b406-ad187ba9748d" />

## Quick start

Devbox is the recommended local development path. It installs Node.js, uv, and
PostgreSQL 16 with pgvector, then runs the database, API server, and web server
with `devbox services`.

```bash
cp .env.example .env
devbox services up
```

Server API: <http://localhost:8000/docs>

Web client: <http://localhost:5173>

```bash
devbox services up --background
```

Docker is still available if you need an isolated container run:

```bash
docker compose up --build
```

## Development

```bash
devbox shell

cd apps/server
uv run --extra dev pytest

cd ../web
npm install
npm run build
```

## Graph principles

1. **Contributions are first-class nodes.** The top-level experience starts from what the authors claim they contributed.
2. **Every node and edge is traceable.** Generated graph objects cite semantic units, which point back to source blocks, page ranges, confidence, source type, and creator metadata.
3. **The graph is directed and shared.** Figures, equations, tables, experiments, and references can support multiple contributions.
4. **Human edits are patches.** Corrections are stored as operations instead of overwriting automatic extraction output.
5. **Reference recursion is bounded.** The default depth is 1, with max-paper and cycle-detection policies.
