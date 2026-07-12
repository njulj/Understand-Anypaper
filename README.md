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

```bash
devbox services up --background
```

## Desktop packaging

The repo now includes an Electron shell for `apps/web` plus a PyInstaller-based
desktop backend for `apps/server`.

### Development

Keep the existing web workflow for day-to-day work:

```bash
cp .env.example .env
devbox services up
```

If you want to view the UI inside Electron during development, start the web and
API servers first, then launch Electron from `apps/web`:

```bash
npm install
npm run electron:dev
```

Electron dev mode reuses `http://127.0.0.1:5173` for the renderer and
`http://127.0.0.1:8000` for the API by default.

When running the packaged desktop app, OpenAI-compatible API settings can be
managed from the in-app toolbar instead of relying on shell environment
variables. The desktop shell stores those values locally and the backend picks
them up for subsequent uploads.

### Production desktop builds

Desktop builds package the FastAPI backend into a standalone executable and make
Electron start it locally on `127.0.0.1:8765`. The packaged backend defaults to
`DATABASE_URL=memory`, so it does not require PostgreSQL on end-user machines.
For local builds, the packaging scripts default to unsigned app artifacts so
they still work offline or on machines without a working timestamp/signing
setup. On macOS, the local script also defaults to `zip` output and skips `dmg`
unless you explicitly enable it.

Build on the target platform:

```bash
./scripts/package-desktop-macos.sh
```

```powershell
.\scripts\package-desktop-windows.ps1
```

Enable signing explicitly when needed:

```bash
PAG_MAC_SIGN=1 ./scripts/package-desktop-macos.sh
```

```powershell
$env:PAG_WINDOWS_SIGN="1"
.\scripts\package-desktop-windows.ps1
```

Enable DMG generation explicitly on macOS:

```bash
PAG_MAC_DMG=1 ./scripts/package-desktop-macos.sh
```

Outputs:

- Intermediate backend executable: `apps/web/backend/server` or `server.exe`
- Electron installers and archives: `apps/web/release/`

The desktop app inherits `OPENAI_API_KEY` / `PAG_OPENAI_API_KEY` from the launch
environment, so keep those configured when testing packaged builds.
<http://localhost:5173>

Graph generation requires an LLM API key in `.env`.
