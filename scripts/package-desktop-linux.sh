#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI_DIR="$ROOT_DIR/apps/cli"
SERVER_DIR="$ROOT_DIR/apps/server"
WEB_DIR="$ROOT_DIR/apps/web"
RELEASE_DIR="$ROOT_DIR/release"
STAGING_DIR="$RELEASE_DIR/staging"
BACKEND_DIR="$STAGING_DIR/backend"
LAUNCHER_DIR="$STAGING_DIR/launcher"
PYINSTALLER_TMP="$ROOT_DIR/.tmp/pyinstaller/linux"
UV_CACHE_DIR="$ROOT_DIR/.tmp/uv-cache"
GO_CACHE_DIR="$ROOT_DIR/.tmp/go-build-cache/linux"
REBUILD_BACKEND="${PAG_REBUILD_BACKEND:-0}"
BACKEND_EXECUTABLE="$BACKEND_DIR/server"
LAUNCHER_EXECUTABLE="$LAUNCHER_DIR/uap"
BACKEND_STAMP_FILE="$BACKEND_DIR/.packaging-version"
RELEASE_BACKEND_EXECUTABLE="$RELEASE_DIR/linux-unpacked/resources/backend/server"
RELEASE_BACKEND_STAMP_FILE="$RELEASE_DIR/linux-unpacked/resources/backend/.packaging-version"
REQUIRED_BACKEND_PACKAGING_VERSION="desktop-backend-v3"

mkdir -p "$BACKEND_DIR" "$LAUNCHER_DIR" "$PYINSTALLER_TMP" "$UV_CACHE_DIR" "$GO_CACHE_DIR" "$RELEASE_DIR"

if [[ ! -d "$WEB_DIR/node_modules" ]]; then
  npm --prefix "$WEB_DIR" install
fi

npm --prefix "$WEB_DIR" run doctor:bindings

backend_rebuild_required() {
  if [[ "$REBUILD_BACKEND" == "1" ]]; then
    return 0
  fi

  if [[ ! -x "$BACKEND_EXECUTABLE" || ! -f "$BACKEND_STAMP_FILE" ]]; then
    return 0
  fi

  if [[ "$(cat "$BACKEND_STAMP_FILE")" != "$REQUIRED_BACKEND_PACKAGING_VERSION" ]]; then
    return 0
  fi

  if [[ "$SERVER_DIR/pyproject.toml" -nt "$BACKEND_EXECUTABLE" || "$0" -nt "$BACKEND_EXECUTABLE" ]]; then
    return 0
  fi

  if find "$SERVER_DIR/understand_anypaper" -type f -name '*.py' -newer "$BACKEND_EXECUTABLE" -print -quit | grep -q .; then
    return 0
  fi

  return 1
}

if [[ ! -x "$BACKEND_EXECUTABLE" && -x "$RELEASE_BACKEND_EXECUTABLE" && -f "$RELEASE_BACKEND_STAMP_FILE" ]] && \
  [[ "$(cat "$RELEASE_BACKEND_STAMP_FILE")" == "$REQUIRED_BACKEND_PACKAGING_VERSION" ]]; then
  echo "Restoring packaged backend from existing release artifact at $RELEASE_BACKEND_EXECUTABLE."
  cp "$RELEASE_BACKEND_EXECUTABLE" "$BACKEND_EXECUTABLE"
  cp "$RELEASE_BACKEND_STAMP_FILE" "$BACKEND_STAMP_FILE"
  chmod +x "$BACKEND_EXECUTABLE"
fi

if backend_rebuild_required; then
  echo "Rebuilding packaged backend to match current desktop packaging inputs."
  rm -rf "${BACKEND_DIR:?}"/*
  env UV_CACHE_DIR="$UV_CACHE_DIR" uv run --project "$SERVER_DIR" --with pyinstaller pyinstaller \
    --noconfirm \
    --clean \
    --onefile \
    --name server \
    --hidden-import agent_framework.openai \
    --hidden-import agent_framework_openai \
    --collect-all agent_framework_openai \
    --distpath "$BACKEND_DIR" \
    --workpath "$PYINSTALLER_TMP/build" \
    --specpath "$PYINSTALLER_TMP/spec" \
    --paths "$SERVER_DIR" \
    "$SERVER_DIR/understand_anypaper/desktop_server.py"
  printf '%s\n' "$REQUIRED_BACKEND_PACKAGING_VERSION" > "$BACKEND_STAMP_FILE"
else
  echo "Reusing existing packaged backend at $BACKEND_EXECUTABLE (set PAG_REBUILD_BACKEND=1 to rebuild)."
fi

echo "Building Go desktop launcher."
(
  cd "$CLI_DIR"
  env GOCACHE="$GO_CACHE_DIR" go build -o "$LAUNCHER_EXECUTABLE" ./cmd
)

npm --prefix "$WEB_DIR" run build

echo "Building Linux AppImage."
npm --prefix "$WEB_DIR" run electron:dist -- --linux AppImage
