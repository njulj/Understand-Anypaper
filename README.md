# Understand Anypaper

Understand Anypaper turns research papers into an interactive **Paper Argument Graph (PAG)**: a directed, evidence-backed graph that connects each contribution with its motivation, research gap, method, equations, figures, experiments, results, conclusions, and references.

<img width="4528" height="2652" alt="image" src="https://github.com/user-attachments/assets/16a0857e-476f-4338-b406-ad187ba9748d" />

## Quick start

[Devbox](https://github.com/jetify-com/devbox) is the recommended local development path. It installs Node.js, uv, and
PostgreSQL 16 with pgvector, then runs the database, API server, and web server
with `devbox services`.

```bash
cp .env.example .env
devbox services up
```

Web client: <http://localhost:5173>

```bash
devbox services up --background
```
