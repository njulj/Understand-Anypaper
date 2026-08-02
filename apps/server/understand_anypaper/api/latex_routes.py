from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from contextlib import suppress
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from understand_anypaper.analyzers.paper_graph_agent import PaperGraphAgent
from understand_anypaper.api.routes import get_store
from understand_anypaper.config import apply_desktop_api_overrides, settings
from understand_anypaper.graph.schema import PaperArgumentGraph
from understand_anypaper.latex.compiler import LatexCompiler
from understand_anypaper.latex.project_store import LatexProject, LatexProjectStore
from understand_anypaper.parser.pdf_parser import PdfParser


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/latex-projects", tags=["latex-projects"])

_project_store: LatexProjectStore | None = None
_project_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def get_project_store() -> LatexProjectStore:
    global _project_store
    if _project_store is None:
        _project_store = LatexProjectStore()
    return _project_store


class OpenFolderRequest(BaseModel):
    path: str


class MainTexRequest(BaseModel):
    main_tex: str


def _project_payload(project: LatexProject) -> dict:
    graph_ready = get_store().get_graph(project.paper_id) is not None
    return {**project.model_dump(mode="json"), "graph_ready": graph_ready}


def _progress_line(event: str, progress: int, message: str, **payload: object) -> str:
    return json.dumps(
        {"event": event, "progress": progress, "message": message, **payload},
        ensure_ascii=False,
    ) + "\n"


@router.get("")
def list_latex_projects() -> list[dict]:
    return [_project_payload(project) for project in get_project_store().list()]


@router.post("/import")
async def import_latex_project(file: Annotated[UploadFile, File(...)]) -> dict:
    filename = file.filename or "latex-project.zip"
    if Path(filename).suffix.casefold() != ".zip":
        raise HTTPException(status_code=422, detail="LaTeX project upload must be a .zip file")
    try:
        project = await asyncio.to_thread(
            get_project_store().create_from_zip,
            filename,
            await file.read(),
        )
    except ValueError as exc:
        logger.warning("Rejected LaTeX ZIP %s: %s", filename, exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        logger.exception("Failed to import LaTeX ZIP %s", filename)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _project_payload(project)


@router.post("/open")
async def open_latex_folder(request: OpenFolderRequest) -> dict:
    try:
        project = await asyncio.to_thread(get_project_store().create_from_folder, request.path)
    except ValueError as exc:
        logger.warning("Rejected LaTeX folder %s: %s", request.path, exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        logger.exception("Failed to open LaTeX folder %s", request.path)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _project_payload(project)


@router.get("/{project_id}")
def get_latex_project(project_id: str) -> dict:
    try:
        return _project_payload(get_project_store().require(project_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="LaTeX project not found") from exc


@router.patch("/{project_id}/main-tex")
def set_latex_main_tex(project_id: str, request: MainTexRequest) -> dict:
    try:
        project = get_project_store().set_main_tex(project_id, request.main_tex)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="LaTeX project not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _project_payload(project)


@router.delete("/{project_id}")
def forget_latex_project(project_id: str) -> dict:
    try:
        project_store = get_project_store()
        project = project_store.require(project_id)
        get_store().delete_paper(project.paper_id)
        project_store.forget(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="LaTeX project not found") from exc
    return {"forgotten": project_id, "source_kind": project.source_kind}


@router.post("/{project_id}/compile")
async def compile_latex_project(project_id: str) -> dict:
    try:
        project = get_project_store().require(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="LaTeX project not found") from exc
    async with _project_locks[project_id]:
        try:
            result = await asyncio.to_thread(
                LatexCompiler(get_project_store()).compile,
                project,
            )
        except ValueError as exc:
            logger.warning("Rejected LaTeX compile for project %s: %s", project_id, exc)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (OSError, RuntimeError) as exc:
            logger.exception("Failed to compile LaTeX project %s", project_id)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "project": _project_payload(get_project_store().require(project_id)),
        "compiler": result.compiler,
        "log": result.log,
    }


@router.post("/{project_id}/graph/update")
async def update_latex_project_graph(project_id: str) -> StreamingResponse:
    try:
        get_project_store().require(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="LaTeX project not found") from exc

    async def progress_stream():
        async with _project_locks[project_id]:
            project_store = get_project_store()
            try:
                apply_desktop_api_overrides(settings)
                project = project_store.require(project_id)
                source_tree = await asyncio.to_thread(
                    project_store.snapshot_tree,
                    project_id,
                )
                yield _progress_line("compiling", 8, "Compiling the LaTeX project.")
                compiled = await asyncio.to_thread(
                    LatexCompiler(project_store).compile,
                    project,
                )
                compiled_tree = await asyncio.to_thread(
                    project_store.snapshot_tree,
                    project_id,
                )
                if compiled_tree != source_tree:
                    raise RuntimeError(
                        "LaTeX sources changed during compilation; retry to build a consistent graph"
                    )
                yield _progress_line(
                    "compiled",
                    24,
                    f"Compiled with {compiled.compiler}.",
                )

                parsed = await asyncio.to_thread(
                    PdfParser().parse,
                    compiled.pdf_path,
                    paper_id=project.paper_id,
                )
                parsed.metadata.update(
                    {
                        "latex_project_id": project.project_id,
                        "latex_root_path": project.root_path,
                        "source_filename": Path(project.main_tex or "paper.tex").name,
                        "source_media_type": "application/x-latex",
                    }
                )
                yield _progress_line(
                    "parsed",
                    38,
                    "Parsed the compiled PDF.",
                    page_count=len(parsed.pages),
                )

                previous_payload = await asyncio.to_thread(
                    project_store.read_authoring_graph,
                    project_id,
                )
                previous_graph = (
                    PaperArgumentGraph.model_validate(previous_payload)
                    if previous_payload is not None
                    else None
                )
                source_diff = await asyncio.to_thread(
                    project_store.diff_from_baseline,
                    project_id,
                )
                activity_queue: asyncio.Queue[dict | None] = asyncio.Queue()

                async def report_agent_activity(activity: dict) -> None:
                    await activity_queue.put(activity)

                async def build_graph():
                    try:
                        return await PaperGraphAgent().build_with_authoring(
                            parsed,
                            initial_graph=previous_graph,
                            source_diff=source_diff,
                            on_progress=report_agent_activity,
                        )
                    finally:
                        await activity_queue.put(None)

                yield _progress_line(
                    "started_graph_agent",
                    46,
                    "Updating the Paper Argument Graph.",
                )
                graph_task = asyncio.create_task(build_graph())
                try:
                    while True:
                        activity = await activity_queue.get()
                        if activity is None:
                            break
                        yield _progress_line(
                            "agent_activity",
                            64,
                            str(activity.get("label", "Graph agent is working.")),
                            activity=activity,
                        )
                    build_result = await graph_task
                finally:
                    if not graph_task.done():
                        graph_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await graph_task
                completed_tree = await asyncio.to_thread(
                    project_store.snapshot_tree,
                    project_id,
                )
                if completed_tree != source_tree:
                    raise RuntimeError(
                        "LaTeX sources changed while the graph was updating; retry to include the latest edits"
                    )

                store = get_store()
                await asyncio.to_thread(store.save_paper, parsed, build_result.graph)
                pdf_bytes = await asyncio.to_thread(compiled.pdf_path.read_bytes)
                await asyncio.to_thread(
                    store.save_source_document,
                    project.paper_id,
                    f"{Path(project.main_tex or 'paper').stem}.pdf",
                    "application/pdf",
                    pdf_bytes,
                )
                revision_id = await asyncio.to_thread(
                    project_store.write_authoring_graph,
                    project_id,
                    build_result.authoring_graph.model_dump(mode="json"),
                )
                project = await asyncio.to_thread(
                    project_store.commit_baseline,
                    project_id,
                    f"Update paper graph {revision_id[:8]}",
                    expected_tree=source_tree,
                )
                project.current_graph_revision = revision_id
                project = await asyncio.to_thread(project_store.save, project)
                yield _progress_line(
                    "complete",
                    100,
                    "Paper graph updated.",
                    project=_project_payload(project),
                    graph=build_result.graph.model_dump(mode="json"),
                )
            except Exception as exc:  # noqa: BLE001 - preserve stream error contract
                logger.exception("Failed to update LaTeX project graph")
                yield _progress_line("error", 100, f"Graph update failed: {exc}")

    return StreamingResponse(progress_stream(), media_type="application/x-ndjson")
