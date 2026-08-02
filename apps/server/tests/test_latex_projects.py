from __future__ import annotations

import asyncio
import io
import zipfile
from pathlib import Path

import pytest

from understand_anypaper.api import latex_routes, routes
from understand_anypaper.config import Settings
from understand_anypaper.latex.project_store import LatexProjectStore
from understand_anypaper.storage import InMemoryGraphStore


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.sqlite'}",
        document_store_dir=str(tmp_path / "documents"),
        latex_project_store_dir=str(tmp_path / "latex-projects"),
    )


def _latex_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "demo/main.tex",
            "\\documentclass{article}\n\\begin{document}\nHello\\end{document}\n",
        )
        archive.writestr("demo/sections/intro.tex", "Introduction\n")
        archive.writestr("demo/.git/config", "uploaded git metadata is ignored")
        archive.writestr("__MACOSX/demo/._main.tex", "AppleDouble metadata is ignored")
        archive.writestr(".DS_Store", "Finder metadata is ignored")
    return buffer.getvalue()


def test_imported_project_tracks_diff_and_commits_baseline(tmp_path: Path):
    store = LatexProjectStore(_settings(tmp_path))
    project = store.create_from_zip("demo.zip", _latex_zip())

    assert project.source_kind == "managed"
    assert project.main_tex == "main.tex"
    assert project.tex_files == ["main.tex", "sections/intro.tex"]
    assert project.baseline_commit
    assert not (Path(project.root_path) / ".git").exists()

    main_tex = Path(project.root_path) / "main.tex"
    main_tex.write_text(main_tex.read_text("utf-8").replace("Hello", "Updated"), "utf-8")
    diff = store.diff_from_baseline(project.project_id)
    assert "-Hello" in diff
    assert "+Updated" in diff

    new_section = Path(project.root_path) / "sections" / "results.tex"
    new_section.write_text("New experimental results\n", "utf-8")
    diff = store.diff_from_baseline(project.project_id)
    assert "sections/results.tex" in diff
    assert "+New experimental results" in diff

    snapshot = store.snapshot_tree(project.project_id)
    new_section.write_text("Results changed during graph update\n", "utf-8")
    with pytest.raises(RuntimeError, match="sources changed"):
        store.commit_baseline(project.project_id, "Stale update", expected_tree=snapshot)
    assert store.require(project.project_id).baseline_commit == project.baseline_commit

    updated = store.commit_baseline(project.project_id, "Update graph")
    assert updated.baseline_commit != project.baseline_commit
    assert store.diff_from_baseline(project.project_id) == ""


def test_forgetting_external_project_keeps_source_folder(tmp_path: Path):
    source = tmp_path / "external-paper"
    source.mkdir()
    (source / "paper.tex").write_text("\\documentclass{article}\n", "utf-8")
    store = LatexProjectStore(_settings(tmp_path))

    project = store.create_from_folder(str(source))
    forgotten = store.forget(project.project_id)

    assert forgotten.source_kind == "external"
    assert source.is_dir()
    assert (source / "paper.tex").is_file()
    assert store.get(project.project_id) is None


def test_invalid_import_does_not_leave_project_state(tmp_path: Path):
    store = LatexProjectStore(_settings(tmp_path))

    with pytest.raises(ValueError, match="invalid ZIP"):
        store.create_from_zip("broken.zip", b"not a zip")

    assert store.list() == []
    assert list(store.root.iterdir()) == []


def test_latex_project_routes_import_and_select_main_tex(tmp_path: Path, monkeypatch):
    store = LatexProjectStore(_settings(tmp_path))
    monkeypatch.setattr(latex_routes, "_project_store", store)
    monkeypatch.setattr(routes, "_store", InMemoryGraphStore())

    async def run_inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(latex_routes.asyncio, "to_thread", run_inline)

    class MemoryUpload:
        filename = "demo.zip"

        async def read(self) -> bytes:
            return _latex_zip()

    upload = MemoryUpload()
    project = asyncio.run(latex_routes.import_latex_project(upload))
    assert project["main_tex"] == "main.tex"

    selected = latex_routes.set_latex_main_tex(
        project["project_id"],
        latex_routes.MainTexRequest(main_tex="sections/intro.tex"),
    )
    assert selected["main_tex"] == "sections/intro.tex"
