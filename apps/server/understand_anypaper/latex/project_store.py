from __future__ import annotations

import json
import os
import shutil
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from understand_anypaper.config import Settings, settings


_GIT_EXCLUDES = """# Understand Anypaper generated files
*.aux
*.bbl
*.bcf
*.blg
*.fdb_latexmk
*.fls
*.log
*.out
*.run.xml
*.synctex.gz
*.toc
_minted-*/
"""


class LatexProject(BaseModel):
    project_id: str
    paper_id: str
    name: str
    root_path: str
    source_kind: Literal["managed", "external"]
    main_tex: str | None = None
    tex_files: list[str] = Field(default_factory=list)
    baseline_commit: str | None = None
    current_graph_revision: str | None = None
    created_at: str
    updated_at: str


class LatexProjectStore:
    """File-backed project registry with sidecar build and Git state."""

    def __init__(self, config: Settings = settings) -> None:
        configured = config.latex_project_store_dir.strip()
        if configured:
            root = Path(configured).expanduser()
        else:
            root = Path(config.document_store_dir).expanduser().parent / "latex-projects"
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[LatexProject]:
        projects: list[LatexProject] = []
        for metadata_path in self.root.glob("*/project.json"):
            try:
                projects.append(LatexProject.model_validate_json(metadata_path.read_text("utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(projects, key=lambda item: item.updated_at, reverse=True)

    def get(self, project_id: str) -> LatexProject | None:
        metadata_path = self._state_dir(project_id) / "project.json"
        if not metadata_path.is_file():
            return None
        project = LatexProject.model_validate_json(metadata_path.read_text("utf-8"))
        refreshed = self._refresh_tex_files(project)
        if refreshed != project:
            self.save(refreshed)
        return refreshed

    def create_from_zip(self, filename: str, data: bytes) -> LatexProject:
        project_id = str(uuid4())
        state_dir = self._state_dir(project_id)
        source_dir = state_dir / "source"
        try:
            source_dir.mkdir(parents=True)
            with NamedTemporaryFile(suffix=".zip") as archive_file:
                archive_file.write(data)
                archive_file.flush()
                with zipfile.ZipFile(archive_file.name) as archive:
                    self._extract_archive(archive, source_dir)
            source_dir = self._collapse_single_root(source_dir)
            project = self._new_project(
                project_id=project_id,
                name=Path(filename).stem or source_dir.name,
                root=source_dir,
                source_kind="managed",
            )
            self._initialize_git(project)
            self.save(project)
            return project
        except zipfile.BadZipFile as exc:
            shutil.rmtree(state_dir, ignore_errors=True)
            raise ValueError("invalid ZIP archive") from exc
        except Exception:
            shutil.rmtree(state_dir, ignore_errors=True)
            raise

    def create_from_folder(self, folder: str) -> LatexProject:
        root = Path(folder).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("selected LaTeX folder does not exist")
        for existing in self.list():
            if Path(existing.root_path) == root:
                return existing
        project_id = str(uuid4())
        project = self._new_project(
            project_id=project_id,
            name=root.name,
            root=root,
            source_kind="external",
        )
        try:
            self._initialize_git(project)
            self.save(project)
            return project
        except Exception:
            shutil.rmtree(self._state_dir(project_id), ignore_errors=True)
            raise

    def save(self, project: LatexProject) -> LatexProject:
        project.updated_at = datetime.now(UTC).isoformat()
        state_dir = self._state_dir(project.project_id)
        state_dir.mkdir(parents=True, exist_ok=True)
        destination = state_dir / "project.json"
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(project.model_dump_json(indent=2) + "\n", encoding="utf-8")
        temporary.replace(destination)
        return project

    def set_main_tex(self, project_id: str, main_tex: str) -> LatexProject:
        project = self.require(project_id)
        relative = self._resolve_source_relative(project, main_tex)
        if relative.suffix.casefold() != ".tex" or not relative.is_file():
            raise ValueError("main_tex must name an existing .tex file in the project")
        project.main_tex = relative.relative_to(Path(project.root_path)).as_posix()
        return self.save(project)

    def require(self, project_id: str) -> LatexProject:
        project = self.get(project_id)
        if project is None:
            raise KeyError(project_id)
        return project

    def forget(self, project_id: str) -> LatexProject:
        project = self.require(project_id)
        state_dir = self._state_dir(project_id)
        if project.source_kind == "managed":
            shutil.rmtree(state_dir)
        else:
            shutil.rmtree(state_dir, ignore_errors=True)
        return project

    def state_dir(self, project_id: str) -> Path:
        self.require(project_id)
        return self._state_dir(project_id)

    def build_dir(self, project_id: str) -> Path:
        path = self.state_dir(project_id) / "build"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def authoring_graph_path(self, project_id: str) -> Path:
        return self.state_dir(project_id) / "authoring-graph.json"

    def read_authoring_graph(self, project_id: str) -> dict | None:
        path = self.authoring_graph_path(project_id)
        return json.loads(path.read_text("utf-8")) if path.is_file() else None

    def write_authoring_graph(self, project_id: str, payload: dict) -> str:
        revision_id = str(uuid4())
        revision_dir = self.state_dir(project_id) / "graph-revisions"
        revision_dir.mkdir(exist_ok=True)
        revision_path = revision_dir / f"{revision_id}.json"
        revision_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            "utf-8",
        )
        path = self.authoring_graph_path(project_id)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
        temporary.replace(path)
        return revision_id

    def diff_from_baseline(self, project_id: str) -> str:
        project = self.require(project_id)
        if not project.baseline_commit:
            return ""
        # The sidecar index is private implementation state. Staging here makes
        # newly created source files visible in the diff without touching a
        # repository (if any) that belongs to the user.
        self._git(project, "add", "-A")
        return self._git(
            project,
            "diff",
            "--no-ext-diff",
            project.baseline_commit,
            "--",
        ).stdout

    def snapshot_tree(self, project_id: str) -> str:
        project = self.require(project_id)
        self._git(project, "add", "-A")
        return self._git(project, "write-tree").stdout.strip()

    def commit_baseline(
        self,
        project_id: str,
        message: str,
        *,
        expected_tree: str | None = None,
    ) -> LatexProject:
        project = self.require(project_id)
        self._git(project, "add", "-A")
        current_tree = self._git(project, "write-tree").stdout.strip()
        if expected_tree is not None and current_tree != expected_tree:
            raise RuntimeError(
                "LaTeX sources changed while the graph was updating; retry to include the latest edits"
            )
        status = self._git(project, "status", "--porcelain").stdout.strip()
        if status:
            self._git(project, "commit", "--no-gpg-sign", "-m", message)
        head = self._git(project, "rev-parse", "HEAD").stdout.strip()
        project.baseline_commit = head
        return self.save(project)

    def _new_project(
        self,
        *,
        project_id: str,
        name: str,
        root: Path,
        source_kind: Literal["managed", "external"],
    ) -> LatexProject:
        tex_files = self._tex_files(root)
        if not tex_files:
            raise ValueError("LaTeX project does not contain any .tex files")
        now = datetime.now(UTC).isoformat()
        return LatexProject(
            project_id=project_id,
            paper_id=str(uuid4()),
            name=name,
            root_path=str(root),
            source_kind=source_kind,
            main_tex=self._detect_main_tex(root, tex_files),
            tex_files=tex_files,
            created_at=now,
            updated_at=now,
        )

    def _initialize_git(self, project: LatexProject) -> None:
        git_dir = self._git_dir(project.project_id)
        git_dir.mkdir(parents=True, exist_ok=True)
        self._git(project, "init")
        self._git(project, "config", "user.name", "Understand Anypaper")
        self._git(project, "config", "user.email", "local@understand-anypaper.invalid")
        info_dir = git_dir / "info"
        info_dir.mkdir(exist_ok=True)
        (info_dir / "exclude").write_text(_GIT_EXCLUDES, "utf-8")
        self._git(project, "add", "-A")
        self._git(project, "commit", "--allow-empty", "--no-gpg-sign", "-m", "Import LaTeX project")
        project.baseline_commit = self._git(project, "rev-parse", "HEAD").stdout.strip()

    def _git(self, project: LatexProject, *args: str) -> subprocess.CompletedProcess[str]:
        command = [
            "git",
            f"--git-dir={self._git_dir(project.project_id)}",
            f"--work-tree={Path(project.root_path)}",
            *args,
        ]
        try:
            return subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"},
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
            raise RuntimeError(f"sidecar Git failed: {detail}") from exc

    def _state_dir(self, project_id: str) -> Path:
        try:
            normalized = str(UUID(project_id))
        except ValueError as exc:
            raise KeyError(project_id) from exc
        return self.root / normalized

    def _git_dir(self, project_id: str) -> Path:
        return self._state_dir(project_id) / "git"

    @staticmethod
    def _extract_archive(archive: zipfile.ZipFile, destination: Path) -> None:
        for info in archive.infolist():
            relative = PurePosixPath(info.filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"ZIP member escapes the project directory: {info.filename}")
            if (
                not relative.parts
                or {".git", ".git-understand-anypaper", "__MACOSX"}.intersection(relative.parts)
                or relative.name == ".DS_Store"
                or relative.name.startswith("._")
            ):
                continue
            target = destination.joinpath(*relative.parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)

    @staticmethod
    def _collapse_single_root(source_dir: Path) -> Path:
        entries = list(source_dir.iterdir())
        if len(entries) != 1 or not entries[0].is_dir():
            return source_dir
        nested = entries[0]
        staging = source_dir.with_name(source_dir.name + "-nested")
        nested.replace(staging)
        source_dir.rmdir()
        staging.replace(source_dir)
        return source_dir

    @staticmethod
    def _tex_files(root: Path) -> list[str]:
        return sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*.tex")
            if path.is_file()
        )

    @staticmethod
    def _detect_main_tex(root: Path, tex_files: list[str]) -> str | None:
        candidates: list[str] = []
        for relative in tex_files:
            try:
                content = (root / relative).read_text("utf-8", errors="replace")
            except OSError:
                continue
            if "\\documentclass" in content:
                candidates.append(relative)
        if "main.tex" in candidates:
            return "main.tex"
        return candidates[0] if len(candidates) == 1 else None

    def _refresh_tex_files(self, project: LatexProject) -> LatexProject:
        root = Path(project.root_path)
        tex_files = self._tex_files(root) if root.is_dir() else []
        main_tex = project.main_tex if project.main_tex in tex_files else self._detect_main_tex(root, tex_files)
        return project.model_copy(update={"tex_files": tex_files, "main_tex": main_tex})

    @staticmethod
    def _resolve_source_relative(project: LatexProject, relative: str) -> Path:
        root = Path(project.root_path).resolve()
        target = (root / relative).resolve()
        if not target.is_relative_to(root):
            raise ValueError("path escapes the LaTeX project")
        return target
