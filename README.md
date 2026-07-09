# Understand Anypaper

[中文](README.cn.md)

Understand Anypaper helps you read research papers by turning them into an interactive **Paper Argument Graph**.

Instead of giving you another linear summary, it shows how the paper's ideas are connected: what the authors claim, why the claim matters, how they implement it, and what evidence supports it.

[Screencast_20260709_175345_github.webm](https://github.com/user-attachments/assets/a3382678-4e64-4f9a-a2ff-e11821fdd92c)


<img width="3394" height="1940" alt="image" src="https://github.com/user-attachments/assets/e02b1854-1b46-445f-b4ce-9d4fc2c457cc" />
<img width="3394" height="1940" alt="image" src="https://github.com/user-attachments/assets/c7ba7435-a302-467b-a1d4-1e36f54c3b95" />

## Why use it?

Research papers are hard to read because the important logic is scattered across the abstract, introduction, method, equations, figures, experiments, and references. Understand Anypaper reorganizes that logic around the paper's actual contributions.

For each contribution, you can explore:

- **Why it exists**: the motivation, problem, research gap, and prior work behind it.
- **How it works**: the method, modules, formulas, algorithms, and figures that implement it.
- **How it is proven**: the datasets, metrics, experiments, ablations, results, tables, and conclusions that support it.

This makes it easier to answer questions like:

- What are the paper's real contributions?
- Which evidence supports each contribution?
- Where exactly does the paper say this?
- Which formulas, figures, or experiments matter most?
- What previous work does this build on?
- Is a contribution well supported, or is some evidence missing?

## Features

- Upload a paper and generate an interactive argument graph.
- View the paper, graph, and node details side by side.
- Click graph nodes to jump back to the original evidence in the paper.
- Inspect contribution-centered subgraphs instead of reading the whole paper at once.
- Trace every node back to source text, page location, and evidence units.
- Search and filter the graph while studying.
- Correct the graph manually when the model gets something wrong.
- Save analyzed papers and come back to them later.

## Quick start

[Devbox](https://github.com/jetify-com/devbox) is the recommended local development path.

```bash
cp .env.example .env
devbox services up
```

Open the web app:

<http://localhost:5173>

Graph generation requires an LLM API key in `.env`.
