from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

import uvicorn

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the packaged Understand Anypaper API server.")
    parser.add_argument("--host", default=os.getenv("PAG_DESKTOP_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.getenv("PAG_DESKTOP_PORT", DEFAULT_PORT)))
    parser.add_argument("--document-store-dir", default=os.getenv("PAG_DOCUMENT_STORE_DIR"))
    return parser.parse_args(argv)


def default_document_store_dir() -> Path:
    runtime_root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()
    return runtime_root / "data" / "documents"


def configure_runtime_environment(args: argparse.Namespace) -> None:
    os.environ.setdefault("DATABASE_URL", "memory")
    document_store_dir = Path(args.document_store_dir) if args.document_store_dir else default_document_store_dir()
    os.environ["PAG_DOCUMENT_STORE_DIR"] = str(document_store_dir.resolve())


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    configure_runtime_environment(args)

    from understand_anypaper.main import app

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        loop="asyncio",
        http="h11",
        ws="none",
        reload=False,
    )


if __name__ == "__main__":
    main()
