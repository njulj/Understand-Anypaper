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
OPENVSCODE_DIR="$STAGING_DIR/openvscode-server"
LATEX_BIN_DIR="$STAGING_DIR/latex/bin"
PYINSTALLER_TMP="$ROOT_DIR/.tmp/pyinstaller/macos"
UV_CACHE_DIR="$ROOT_DIR/.tmp/uv-cache"
GO_CACHE_DIR="$ROOT_DIR/.tmp/go-build-cache/macos"
MAC_SIGN_ENABLED="${PAG_MAC_SIGN:-0}"
MAC_DMG_ENABLED="${PAG_MAC_DMG:-0}"
REBUILD_BACKEND="${PAG_REBUILD_BACKEND:-0}"
BACKEND_EXECUTABLE="$BACKEND_DIR/server"
LAUNCHER_EXECUTABLE="$LAUNCHER_DIR/uap"
BACKEND_STAMP_FILE="$BACKEND_DIR/.packaging-version"
RELEASE_BACKEND_EXECUTABLE="$RELEASE_DIR/mac-arm64/Understand Anypaper.app/Contents/Resources/backend/server"
RELEASE_BACKEND_STAMP_FILE="$RELEASE_DIR/mac-arm64/Understand Anypaper.app/Contents/Resources/backend/.packaging-version"
REQUIRED_BACKEND_PACKAGING_VERSION="desktop-backend-v4"

mkdir -p "$BACKEND_DIR" "$LAUNCHER_DIR" "$OPENVSCODE_DIR" "$LATEX_BIN_DIR" "$PYINSTALLER_TMP" "$UV_CACHE_DIR" "$GO_CACHE_DIR" "$RELEASE_DIR"

if [[ -n "${PAG_OPENVSCODE_DIR:-}" ]]; then
  cp -R "$PAG_OPENVSCODE_DIR"/. "$OPENVSCODE_DIR"/
fi
if [[ -n "${PAG_TECTONIC_EXECUTABLE:-}" ]]; then
  cp "$PAG_TECTONIC_EXECUTABLE" "$LATEX_BIN_DIR/tectonic"
  chmod +x "$LATEX_BIN_DIR/tectonic"
fi

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
  rm -rf "$BACKEND_DIR"/*
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

if [[ "$MAC_SIGN_ENABLED" != "1" ]]; then
  echo "Building unsigned macOS app bundle (set PAG_MAC_SIGN=1 to enable codesigning)."
  export CSC_IDENTITY_AUTO_DISCOVERY=false
fi

if [[ "$MAC_DMG_ENABLED" == "1" ]]; then
  echo "Building macOS zip + dmg artifacts (set PAG_MAC_DMG=0 to skip dmg)."
  npm --prefix "$WEB_DIR" run electron:dist -- --mac dmg zip
else
  echo "Building macOS zip artifact only (set PAG_MAC_DMG=1 to also build dmg)."
  npm --prefix "$WEB_DIR" run electron:dist -- --mac zip
fi
