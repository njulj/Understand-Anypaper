# Understand Anypaper CLI Spec

## Goals

The CLI should cover three jobs well:

1. Direct-use local service lifecycle: start, stop, inspect, and auto-start the
   packaged local backend.
2. Study workflow: upload a paper, inspect the resulting graph, search it, and
   read evidence without opening the UI.
3. Workspace workflow: initialize and manage the local shared workspace used by
   both CLI and Electron.

The same end-user commands should work well for both humans and agents.

## UX Principles

- Default output is concise and readable for humans.
- `--json` switches read commands and mutations to stable machine-readable output.
- Errors are actionable and printed to stderr.
- Service commands wrap existing project entry points instead of inventing new runtime logic.

## Command Surface

### Global flags

- `--json`: print final command result as JSON.
- `--api-base-url`: API base URL, default `http://127.0.0.1:8765`.
- `--timeout`: end-to-end timeout, default `30s`.

### Service commands

- `uap service start`
- `uap service stop`
- `uap service status`

These commands manage the direct-use local backend service only.

Behavior:

- `start` starts the local backend service on a fixed local port, default
  `127.0.0.1:8765`.
- `stop` stops that local backend service.
- `status` reports whether the local backend is running and healthy.
- `paper *`, `graph *`, and `node *` commands should auto-start the local
  backend if it is not already running.
- Electron should call the same lifecycle entrypoints instead of owning a
  separate backend process model.

### Workspace commands

- `uap init`

Behavior:

- `init` initializes the default local workspace when no path is specified.
- `init --path <dir>` creates or adopts a workspace at a custom path.
- The CLI should auto-initialize the default workspace when needed by content
  commands.
- Electron should expose the same capability through a graphical onboarding or
  settings flow.

### Paper commands

- `uap paper upload <file>`
- `uap paper list`
- `uap paper show <paper-id>`
- `uap paper delete <paper-id>`

`upload` streams stage progress for humans and returns the final graph payload for agents when `--json` is enabled.

### Graph commands

- `uap graph show <paper-id> [--root <node-id>] [--depth <n>]`
- `uap graph search <paper-id> <query> [--type <node-type>]... [--expand-depth <n>]`

`graph show` renders a text tree using the graph's edge direction so a user can quickly understand the paper structure from a terminal.

### Node commands

- `uap node evidence <paper-id> <node-id>`

This command should surface the exact source snippets attached to a node.

### Desktop commands

- `uap desktop run-backend`

This command is the shared desktop launcher used by both the standalone CLI and Electron.
It should prefer a packaged backend executable when present and fall back to `uv` only in
development environments.

### Shared local data

Direct-use CLI and packaged Electron must share one local data source.

Requirements:

- They must use the same backend endpoint.
- They must use the same workspace selection rules.
- They must use the same persisted graph/document store.
- Restarting the local backend must not lose user data.
- The shared local store must not depend on Postgres or any external service.

This means the current `DATABASE_URL=memory` desktop behavior is not sufficient
for the final direct-use lifecycle.

### Workspace model

The direct-use experience should be based on a shared workspace, not the shell's
current working directory.

Workspace contents:

- `uap.sqlite`
- `documents/`
- `cache/`
- `logs/`
- `settings.json`

The system should also maintain a global user config pointing to the default
workspace.

## Direct-Use Lifecycle

### Local backend lifecycle

1. Electron opens or a CLI content command runs.
2. If the workspace is not initialized, `uap init` logic is invoked.
3. If the local backend is not running, `uap service start` logic is invoked.
4. The local backend starts once and becomes the shared service for both CLI and
   Electron.
5. Closing the Electron window does not stop the backend.
6. Explicit stop happens through Electron UI / tray or `uap service stop`.

### Electron lifecycle

1. Electron is a client shell plus service controller.
2. It can start or stop the shared local backend.
3. It should reconnect to an already-running backend instead of starting a
   second one.
4. Window visibility is independent from backend lifetime.

### CLI lifecycle

1. CLI content commands talk to the shared local backend.
2. If the workspace is unavailable, they auto-initialize it first.
3. If the backend is unavailable, they auto-start it first.
4. `uap service stop` ends the shared local backend lifecycle for both CLI and
   Electron.

## Runtime Strategy

- Direct-use lifecycle should not require `devbox`, Python, or Postgres from the
  user.
- Implementation may still use PyInstaller and a Go launcher internally, but
  users should not have to reason about them.

## Acceptance Criteria

- A human can install the CLI, run `uap paper upload ./paper.pdf`, and have the
  workspace and local backend auto-start without extra dependencies.
- Packaged Electron and CLI commands see the same papers and graph data.
- `uap service start|stop|status` controls the same local backend lifecycle that
  Electron uses.
- `uap init` and the Electron onboarding flow can both create or adopt the same
  workspace layout.
