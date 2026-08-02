#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGING_DIR="$ROOT_DIR/release/staging"
OPENVSCODE_DIR="$STAGING_DIR/openvscode-server"
LATEX_BIN_DIR="$STAGING_DIR/latex/bin"
DOWNLOAD_DIR="$ROOT_DIR/.tmp/tool-downloads"
OPENVSCODE_VERSION="${PAG_OPENVSCODE_VERSION:-1.109.5}"
TECTONIC_VERSION="${PAG_TECTONIC_VERSION:-0.16.9}"

case "$(uname -m)" in
  x86_64)
    OPENVSCODE_ARCH="linux-x64"
    TECTONIC_ARCH="x86_64-unknown-linux-gnu"
    ;;
  aarch64 | arm64)
    OPENVSCODE_ARCH="linux-arm64"
    TECTONIC_ARCH="aarch64-unknown-linux-gnu"
    ;;
  *)
    echo "Unsupported Linux architecture for desktop editor tools: $(uname -m)" >&2
    exit 1
    ;;
esac

mkdir -p "$OPENVSCODE_DIR" "$LATEX_BIN_DIR" "$DOWNLOAD_DIR"

if [[ "${PAG_REFRESH_EDITOR_TOOLS:-0}" == "1" ]]; then
  rm -rf "${OPENVSCODE_DIR:?}"/*
  rm -f "$LATEX_BIN_DIR/tectonic"
fi

if [[ ! -x "$OPENVSCODE_DIR/bin/openvscode-server" ]]; then
  archive="$DOWNLOAD_DIR/openvscode-server-v${OPENVSCODE_VERSION}-${OPENVSCODE_ARCH}.tar.gz"
  url="https://github.com/gitpod-io/openvscode-server/releases/download/openvscode-server-v${OPENVSCODE_VERSION}/$(basename "$archive")"
  echo "Downloading OpenVSCode Server ${OPENVSCODE_VERSION} (${OPENVSCODE_ARCH})."
  curl -fL --retry 3 -o "$archive" "$url"
  tar -xzf "$archive" -C "$OPENVSCODE_DIR" --strip-components=1
fi

if [[ ! -x "$LATEX_BIN_DIR/tectonic" ]]; then
  archive="$DOWNLOAD_DIR/tectonic-${TECTONIC_VERSION}-${TECTONIC_ARCH}.tar.gz"
  url="https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40${TECTONIC_VERSION}/$(basename "$archive")"
  echo "Downloading Tectonic ${TECTONIC_VERSION} (${TECTONIC_ARCH})."
  curl -fL --retry 3 -o "$archive" "$url"
  tar -xzf "$archive" -C "$LATEX_BIN_DIR"
  chmod +x "$LATEX_BIN_DIR/tectonic"
fi

"$OPENVSCODE_DIR/bin/openvscode-server" --version
"$LATEX_BIN_DIR/tectonic" --version
