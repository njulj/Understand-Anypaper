# Understand Anypaper

<img width="3394" height="1940" alt="image" src="https://github.com/user-attachments/assets/e02b1854-1b46-445f-b4ce-9d4fc2c457cc" />
<img width="3394" height="1940" alt="image" src="https://github.com/user-attachments/assets/c7ba7435-a302-467b-a1d4-1e36f54c3b95" />


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
