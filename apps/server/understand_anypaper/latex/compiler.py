from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from understand_anypaper.config import Settings, settings
from understand_anypaper.latex.project_store import LatexProject, LatexProjectStore


@dataclass(frozen=True)
class LatexCompileResult:
    pdf_path: Path
    log: str
    compiler: str


class LatexCompiler:
    def __init__(self, store: LatexProjectStore, config: Settings = settings) -> None:
        self.store = store
        self.config = config

    def compile(self, project: LatexProject) -> LatexCompileResult:
        if not project.main_tex:
            raise ValueError("Select the main .tex file before compiling the project")
        root = Path(project.root_path)
        main_path = root / project.main_tex
        if not main_path.is_file():
            raise ValueError(f"Main TeX file does not exist: {project.main_tex}")
        build_dir = self.store.build_dir(project.project_id)
        compiler = self._select_compiler()
        if compiler == "tectonic":
            command = [
                shutil.which("tectonic") or "tectonic",
                "-X",
                "compile",
                "--keep-logs",
                "--synctex",
                "--outdir",
                str(build_dir),
                project.main_tex,
            ]
        else:
            command = [
                shutil.which("latexmk") or "latexmk",
                "-pdf",
                "-interaction=nonstopmode",
                "-file-line-error",
                f"-outdir={build_dir}",
                project.main_tex,
            ]
        completed = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=self.config.latex_compile_timeout_seconds,
            env=dict(os.environ),
        )
        output = (completed.stdout + "\n" + completed.stderr).strip()
        log_path = build_dir / "compile.log"
        log_path.write_text(output + "\n", encoding="utf-8")
        if completed.returncode != 0:
            raise RuntimeError(output or f"{compiler} exited with code {completed.returncode}")
        generated = build_dir / f"{main_path.stem}.pdf"
        if not generated.is_file():
            raise RuntimeError(f"{compiler} completed without producing {generated.name}")
        current = build_dir / "current.pdf"
        if generated != current:
            shutil.copy2(generated, current)
        return LatexCompileResult(pdf_path=current, log=output, compiler=compiler)

    def _select_compiler(self) -> str:
        configured = self.config.latex_compiler.strip().casefold()
        if configured not in {"", "auto"}:
            if configured not in {"tectonic", "latexmk"}:
                raise ValueError("latex_compiler must be auto, tectonic, or latexmk")
            if shutil.which(configured) is None:
                raise RuntimeError(f"Configured LaTeX compiler is not installed: {configured}")
            return configured
        if shutil.which("tectonic"):
            return "tectonic"
        if shutil.which("latexmk"):
            return "latexmk"
        raise RuntimeError("No LaTeX compiler found. Install tectonic or latexmk.")
