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
- Import a LaTeX ZIP, or open a local source folder in the desktop app, and edit it in an embedded OpenVSCode workspace.
- Recompile the paper and update its graph from the source changes with one button.

## Quick start

[Devbox](https://github.com/jetify-com/devbox) is the recommended local development path.

```bash
cp .env.example .env
devbox services up
```

Open the web app:

<http://localhost:5173>

```bash
devbox services up --background
```

The LaTeX workspace is available at <http://localhost:5173/write>. Devbox also
starts OpenVSCode Server on port `3001` and provides Tectonic for compilation.

## Desktop packaging

The repo now includes an Electron shell for `apps/web`, a Go desktop launcher in
`apps/cli`, and a PyInstaller-based desktop backend for `apps/server`.

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
`http://127.0.0.1:8000` for the API by default. When
`PAG_ELECTRON_SPAWN_BACKEND=1`, Electron now starts the backend through the Go
launcher (`uap desktop run-backend`), which prefers a packaged backend
executable and only falls back to `uv` in development. The desktop shell now
stays resident in the tray/menu bar, so closing the window hides the workspace
instead of shutting down the warmed backend.

When running the packaged desktop app, OpenAI-compatible API settings can be
managed from the in-app toolbar instead of relying on shell environment
variables. The desktop shell stores those values locally and the backend picks
them up for subsequent uploads. The settings panel also controls whether LLM
requests include `prompt_cache_key`; disable it for compatible endpoints that
reject that provider-specific parameter.

On the first packaged launch, Electron now performs a lightweight desktop
onboarding flow before loading the web UI:

- choose the local workspace location used for SQLite, uploaded documents,
  cache, and logs
- optionally install a reusable `uap` command wrapper into a user-selected
  folder

The main webpage is only shown after that initialization succeeds.

### Production desktop builds

Desktop builds package the FastAPI backend into a standalone executable and also
bundle the Go launcher. Linux builds additionally download and bundle the pinned
OpenVSCode Server and Tectonic releases. Electron starts the launcher locally on
`127.0.0.1:8765`, and the launcher starts the packaged backend. The packaged
backend defaults to a workspace-local SQLite database, so it does not require
PostgreSQL on end-user machines.
For local builds, the packaging scripts default to unsigned app artifacts so
they still work offline or on machines without a working timestamp/signing
setup. On macOS, the local script also defaults to `zip` output and skips `dmg`
unless you explicitly enable it.

Build on the target platform:

```bash
./scripts/package-desktop-linux.sh
```

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

- Intermediate backend executable: `release/staging/backend/server` or `server.exe`
- Intermediate launcher executable: `release/staging/launcher/uap` or `uap.exe`
- Electron installers and archives: `release/`

OpenVSCode publishes ready-to-run Linux archives. For experimental macOS or
Windows packaging, provide an already built platform distribution through
`PAG_OPENVSCODE_DIR` and a Tectonic binary through
`PAG_TECTONIC_EXECUTABLE`; without those, the existing reader remains available
but LaTeX editing reports that its local editor/compiler is missing.

## GitHub Releases

The repo includes a desktop packaging workflow at
`.github/workflows/desktop-releases.yml`.

- pushing a `v*` tag builds macOS, Windows, and Linux desktop artifacts
- those artifacts are then attached to the matching GitHub Release
- `workflow_dispatch` can still be used to validate the packaging pipeline
  without publishing a Release

The desktop app inherits `OPENAI_API_KEY` / `PAG_OPENAI_API_KEY` from the launch
environment, so keep those configured when testing packaged builds.

Graph generation requires an LLM API key in `.env`.
