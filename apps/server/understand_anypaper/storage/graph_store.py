import json
import logging
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from understand_anypaper.config import settings
from understand_anypaper.graph.schema import GraphEdge, GraphNode, PaperArgumentGraph
from understand_anypaper.parser.models import PageSourceLocation, PaperReference, ParsedPaper, SemanticUnit
from understand_anypaper.retrieval.embeddings import EmbeddingClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceDocument:
    filename: str
    media_type: str
    data: bytes


class GraphStore(ABC):
    """Persistence contract for parsed papers and their argument graphs."""

    @abstractmethod
    def save_paper(self, parsed: ParsedPaper, graph: PaperArgumentGraph) -> None: ...

    @abstractmethod
    def save_source_document(self, paper_id: str, filename: str, media_type: str, data: bytes) -> None: ...

    @abstractmethod
    def get_source_document(self, paper_id: str) -> SourceDocument | None: ...

    @abstractmethod
    def list_papers(self) -> list[dict]: ...

    @abstractmethod
    def delete_paper(self, paper_id: str) -> bool: ...

    @abstractmethod
    def get_graph(self, paper_id: str) -> PaperArgumentGraph | None: ...

    @abstractmethod
    def replace_graph(self, paper_id: str, graph: PaperArgumentGraph) -> None: ...

    @abstractmethod
    def get_semantic_units(self, paper_id: str) -> list[SemanticUnit]: ...

    @abstractmethod
    def get_references(self, paper_id: str) -> list[PaperReference]: ...

    @abstractmethod
    def find_reference(self, reference_id: str) -> PaperReference | None: ...

    @abstractmethod
    def update_reference(self, reference: PaperReference) -> None: ...

    @abstractmethod
    def record_patch(self, paper_id: str, operations: list[dict]) -> str: ...

    @abstractmethod
    def vector_search(self, paper_id: str, query: str, limit: int = 10) -> list[tuple[str, float]]:
        """Returns (node_id, similarity) pairs; empty when embeddings are unavailable."""


class InMemoryGraphStore(GraphStore):
    def __init__(self) -> None:
        self._papers: dict[str, ParsedPaper] = {}
        self._graphs: dict[str, PaperArgumentGraph] = {}
        self._patches: dict[str, list[dict]] = {}
        self._documents: dict[str, SourceDocument] = {}

    def save_paper(self, parsed: ParsedPaper, graph: PaperArgumentGraph) -> None:
        self._papers[parsed.paper_id] = parsed
        self._graphs[parsed.paper_id] = graph

    def save_source_document(self, paper_id: str, filename: str, media_type: str, data: bytes) -> None:
        self._documents[paper_id] = SourceDocument(filename=filename, media_type=media_type, data=data)
        if paper_id in self._papers:
            self._papers[paper_id].metadata["source_document"] = {
                "filename": filename,
                "media_type": media_type,
                "size": len(data),
            }

    def get_source_document(self, paper_id: str) -> SourceDocument | None:
        return self._documents.get(paper_id)

    def list_papers(self) -> list[dict]:
        return [
            {
                "paper_id": paper.paper_id,
                "title": paper.title,
                "abstract": paper.abstract,
                "metadata": paper.metadata,
            }
            for paper in self._papers.values()
        ]

    def delete_paper(self, paper_id: str) -> bool:
        existed = paper_id in self._papers
        self._papers.pop(paper_id, None)
        self._graphs.pop(paper_id, None)
        self._patches.pop(paper_id, None)
        self._documents.pop(paper_id, None)
        return existed

    def get_graph(self, paper_id: str) -> PaperArgumentGraph | None:
        return self._graphs.get(paper_id)

    def replace_graph(self, paper_id: str, graph: PaperArgumentGraph) -> None:
        self._graphs[paper_id] = graph

    def get_semantic_units(self, paper_id: str) -> list[SemanticUnit]:
        paper = self._papers.get(paper_id)
        return paper.semantic_units if paper else []

    def get_references(self, paper_id: str) -> list[PaperReference]:
        paper = self._papers.get(paper_id)
        return paper.references if paper else []

    def find_reference(self, reference_id: str) -> PaperReference | None:
        for paper in self._papers.values():
            for reference in paper.references:
                if reference.reference_id == reference_id:
                    return reference
        return None

    def update_reference(self, reference: PaperReference) -> None:
        for paper in self._papers.values():
            for index, existing in enumerate(paper.references):
                if existing.reference_id == reference.reference_id:
                    paper.references[index] = reference
                    return

    def record_patch(self, paper_id: str, operations: list[dict]) -> str:
        patch_id = str(uuid4())
        self._patches.setdefault(paper_id, []).append({"id": patch_id, "operations": operations})
        return patch_id

    def vector_search(self, paper_id: str, query: str, limit: int = 10) -> list[tuple[str, float]]:
        return []


class SQLiteGraphStore(GraphStore):
    def __init__(self, database_url: str) -> None:
        self._path = self._database_path_from_url(database_url)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    @staticmethod
    def _database_path_from_url(database_url: str) -> Path:
        if not database_url.startswith("sqlite:///"):
            raise ValueError(f"Unsupported SQLite database URL: {database_url}")
        return Path(database_url.removeprefix("sqlite:///")).expanduser().resolve()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS papers (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    abstract TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS semantic_units (
                    id TEXT PRIMARY KEY,
                    paper_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL DEFAULT '',
                    source_location_json TEXT NOT NULL DEFAULT '{}',
                    confidence REAL NOT NULL DEFAULT 0,
                    created_by TEXT NOT NULL DEFAULT '',
                    properties_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT PRIMARY KEY,
                    paper_id TEXT NOT NULL,
                    node_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    properties_json TEXT NOT NULL DEFAULT '{}',
                    semantic_unit_ids_json TEXT NOT NULL DEFAULT '[]',
                    page_ranges_json TEXT NOT NULL DEFAULT '[]',
                    confidence REAL NOT NULL DEFAULT 0,
                    source_type TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL DEFAULT '',
                    verified INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS edges (
                    id TEXT PRIMARY KEY,
                    paper_id TEXT NOT NULL,
                    source_node_id TEXT NOT NULL,
                    target_node_id TEXT NOT NULL,
                    edge_type TEXT NOT NULL,
                    semantic_unit_ids_json TEXT NOT NULL DEFAULT '[]',
                    confidence REAL NOT NULL DEFAULT 0,
                    inference_type TEXT NOT NULL DEFAULT '',
                    properties_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS paper_references (
                    id TEXT PRIMARY KEY,
                    paper_id TEXT NOT NULL,
                    raw_text TEXT NOT NULL,
                    title TEXT,
                    authors_json TEXT NOT NULL DEFAULT '[]',
                    year INTEGER,
                    doi TEXT,
                    arxiv_id TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS graph_patches (
                    id TEXT PRIMARY KEY,
                    paper_id TEXT NOT NULL,
                    operations_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

    def save_paper(self, parsed: ParsedPaper, graph: PaperArgumentGraph) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO papers (id, title, abstract, metadata_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    abstract = excluded.abstract,
                    metadata_json = excluded.metadata_json
                """,
                (parsed.paper_id, parsed.title, parsed.abstract, json.dumps(parsed.metadata, default=str)),
            )
            for table in ("semantic_units", "nodes", "edges", "paper_references"):
                conn.execute(f"DELETE FROM {table} WHERE paper_id = ?", (parsed.paper_id,))
            for unit in parsed.semantic_units:
                conn.execute(
                    """
                    INSERT INTO semantic_units
                    (id, paper_id, role, title, text, source_location_json, confidence, created_by, properties_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        unit.semantic_unit_id,
                        parsed.paper_id,
                        unit.role,
                        unit.title,
                        unit.text,
                        json.dumps(unit.source_location.model_dump()),
                        unit.confidence,
                        unit.created_by,
                        json.dumps(unit.properties, default=str),
                    ),
                )
            for node in graph.nodes:
                conn.execute(
                    """
                    INSERT INTO nodes
                    (id, paper_id, node_type, title, summary, properties_json, semantic_unit_ids_json, page_ranges_json,
                     confidence, source_type, created_by, verified)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        node.id,
                        node.paper_id,
                        str(node.node_type),
                        node.title,
                        node.summary,
                        json.dumps(node.properties, default=str),
                        json.dumps(node.semantic_unit_ids),
                        json.dumps([list(pair) for pair in node.page_ranges]),
                        node.confidence,
                        node.source_type,
                        node.created_by,
                        int(node.verified),
                    ),
                )
            for edge in graph.edges:
                conn.execute(
                    """
                    INSERT INTO edges
                    (id, paper_id, source_node_id, target_node_id, edge_type, semantic_unit_ids_json, confidence,
                     inference_type, properties_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        edge.id,
                        edge.paper_id,
                        edge.source_node_id,
                        edge.target_node_id,
                        str(edge.edge_type),
                        json.dumps(edge.semantic_unit_ids),
                        edge.confidence,
                        edge.inference_type,
                        json.dumps(edge.properties, default=str),
                    ),
                )
            for reference in parsed.references:
                conn.execute(
                    """
                    INSERT INTO paper_references
                    (id, paper_id, raw_text, title, authors_json, year, doi, arxiv_id, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        reference.reference_id,
                        parsed.paper_id,
                        reference.raw_text,
                        reference.title,
                        json.dumps(reference.authors),
                        reference.year,
                        reference.doi,
                        reference.arxiv_id,
                        json.dumps({"marker": reference.marker}),
                    ),
                )

    def save_source_document(self, paper_id: str, filename: str, media_type: str, data: bytes) -> None:
        directory = Path(settings.document_store_dir).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        suffix = Path(filename).suffix.lower() or ".pdf"
        path = directory / f"{paper_id}{suffix}"
        path.write_bytes(data)
        metadata = {
            "source_document": {
                "filename": filename,
                "media_type": media_type,
                "path": str(path),
                "size": len(data),
            }
        }
        with self._connect() as conn:
            current = conn.execute("SELECT metadata_json FROM papers WHERE id = ?", (paper_id,)).fetchone()
            current_metadata = _loads_json(current["metadata_json"], {}) if current else {}
            current_metadata.update(metadata)
            conn.execute(
                "UPDATE papers SET metadata_json = ? WHERE id = ?",
                (json.dumps(current_metadata, default=str), paper_id),
            )

    def get_source_document(self, paper_id: str) -> SourceDocument | None:
        with self._connect() as conn:
            row = conn.execute("SELECT metadata_json FROM papers WHERE id = ?", (paper_id,)).fetchone()
        if row is None:
            return None
        source = (_loads_json(row["metadata_json"], {}) or {}).get("source_document") or {}
        path_value = source.get("path")
        if not path_value:
            return None
        path = Path(path_value)
        if not path.exists() or not path.is_file():
            return None
        return SourceDocument(
            filename=source.get("filename") or path.name,
            media_type=source.get("media_type") or "application/octet-stream",
            data=path.read_bytes(),
        )

    def list_papers(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, title, abstract, metadata_json, created_at FROM papers ORDER BY created_at DESC"
            ).fetchall()
        return [
            {
                "paper_id": row["id"],
                "title": row["title"],
                "abstract": row["abstract"],
                "metadata": _loads_json(row["metadata_json"], {}),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def delete_paper(self, paper_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT metadata_json FROM papers WHERE id = ?", (paper_id,)).fetchone()
            if row is None:
                return False
            source = (_loads_json(row["metadata_json"], {}) or {}).get("source_document") or {}
            conn.execute("DELETE FROM semantic_units WHERE paper_id = ?", (paper_id,))
            conn.execute("DELETE FROM edges WHERE paper_id = ?", (paper_id,))
            conn.execute("DELETE FROM nodes WHERE paper_id = ?", (paper_id,))
            conn.execute("DELETE FROM paper_references WHERE paper_id = ?", (paper_id,))
            conn.execute("DELETE FROM graph_patches WHERE paper_id = ?", (paper_id,))
            conn.execute("DELETE FROM papers WHERE id = ?", (paper_id,))

        source_path = source.get("path")
        if source_path:
            path = Path(source_path)
            try:
                if path.exists() and path.is_file():
                    path.unlink()
            except OSError as exc:
                logger.warning("Failed to delete source document for %s: %s", paper_id, exc)
        return True

    def get_graph(self, paper_id: str) -> PaperArgumentGraph | None:
        with self._connect() as conn:
            exists = conn.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone()
            if exists is None:
                return None
            node_rows = conn.execute(
                """
                SELECT id, node_type, title, summary, properties_json, semantic_unit_ids_json, page_ranges_json,
                       confidence, source_type, created_by, verified
                FROM nodes WHERE paper_id = ?
                """,
                (paper_id,),
            ).fetchall()
            edge_rows = conn.execute(
                """
                SELECT id, source_node_id, target_node_id, edge_type, semantic_unit_ids_json, confidence,
                       inference_type, properties_json
                FROM edges WHERE paper_id = ?
                """,
                (paper_id,),
            ).fetchall()

        nodes = [
            GraphNode(
                id=row["id"],
                paper_id=paper_id,
                node_type=row["node_type"],
                title=row["title"],
                summary=row["summary"],
                confidence=row["confidence"],
                source_type=row["source_type"],
                semantic_unit_ids=_loads_json(row["semantic_unit_ids_json"], []),
                page_ranges=[tuple(pair) for pair in _loads_json(row["page_ranges_json"], [])],
                properties=_loads_json(row["properties_json"], {}),
                created_by=row["created_by"],
                verified=bool(row["verified"]),
            )
            for row in node_rows
        ]
        edges = [
            GraphEdge(
                id=row["id"],
                paper_id=paper_id,
                source_node_id=row["source_node_id"],
                target_node_id=row["target_node_id"],
                edge_type=row["edge_type"],
                confidence=row["confidence"],
                semantic_unit_ids=_loads_json(row["semantic_unit_ids_json"], []),
                inference_type=row["inference_type"],
                properties=_loads_json(row["properties_json"], {}),
            )
            for row in edge_rows
        ]
        return PaperArgumentGraph(paper_id=paper_id, nodes=nodes, edges=edges)

    def replace_graph(self, paper_id: str, graph: PaperArgumentGraph) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM edges WHERE paper_id = ?", (paper_id,))
            conn.execute("DELETE FROM nodes WHERE paper_id = ?", (paper_id,))
            for node in graph.nodes:
                conn.execute(
                    """
                    INSERT INTO nodes
                    (id, paper_id, node_type, title, summary, properties_json, semantic_unit_ids_json, page_ranges_json,
                     confidence, source_type, created_by, verified)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        node.id,
                        node.paper_id,
                        str(node.node_type),
                        node.title,
                        node.summary,
                        json.dumps(node.properties, default=str),
                        json.dumps(node.semantic_unit_ids),
                        json.dumps([list(pair) for pair in node.page_ranges]),
                        node.confidence,
                        node.source_type,
                        node.created_by,
                        int(node.verified),
                    ),
                )
            for edge in graph.edges:
                conn.execute(
                    """
                    INSERT INTO edges
                    (id, paper_id, source_node_id, target_node_id, edge_type, semantic_unit_ids_json, confidence,
                     inference_type, properties_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        edge.id,
                        edge.paper_id,
                        edge.source_node_id,
                        edge.target_node_id,
                        str(edge.edge_type),
                        json.dumps(edge.semantic_unit_ids),
                        edge.confidence,
                        edge.inference_type,
                        json.dumps(edge.properties, default=str),
                    ),
                )

    def get_semantic_units(self, paper_id: str) -> list[SemanticUnit]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, role, title, text, source_location_json, confidence, created_by, properties_json
                FROM semantic_units WHERE paper_id = ? ORDER BY id
                """,
                (paper_id,),
            ).fetchall()
        return [
            SemanticUnit(
                semantic_unit_id=row["id"],
                paper_id=paper_id,
                role=row["role"],
                title=row["title"],
                text=row["text"],
                source_location=PageSourceLocation(**_loads_json(row["source_location_json"], {})),
                confidence=row["confidence"],
                created_by=row["created_by"],
                properties=_loads_json(row["properties_json"], {}),
            )
            for row in rows
        ]

    def get_references(self, paper_id: str) -> list[PaperReference]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, raw_text, title, authors_json, year, doi, arxiv_id, metadata_json
                FROM paper_references WHERE paper_id = ? ORDER BY id
                """,
                (paper_id,),
            ).fetchall()
        return [self._reference_from_row(row) for row in rows]

    def find_reference(self, reference_id: str) -> PaperReference | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, raw_text, title, authors_json, year, doi, arxiv_id, metadata_json
                FROM paper_references WHERE id = ?
                """,
                (reference_id,),
            ).fetchone()
        return self._reference_from_row(row) if row is not None else None

    @staticmethod
    def _reference_from_row(row: sqlite3.Row) -> PaperReference:
        metadata = _loads_json(row["metadata_json"], {})
        return PaperReference(
            reference_id=row["id"],
            marker=metadata.get("marker"),
            raw_text=row["raw_text"],
            title=row["title"],
            authors=_loads_json(row["authors_json"], []),
            year=row["year"],
            doi=row["doi"],
            arxiv_id=row["arxiv_id"],
        )

    def update_reference(self, reference: PaperReference) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE paper_references
                SET title = ?, authors_json = ?, year = ?, doi = ?, arxiv_id = ?
                WHERE id = ?
                """,
                (
                    reference.title,
                    json.dumps(reference.authors),
                    reference.year,
                    reference.doi,
                    reference.arxiv_id,
                    reference.reference_id,
                ),
            )

    def record_patch(self, paper_id: str, operations: list[dict]) -> str:
        patch_id = str(uuid4())
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO graph_patches (id, paper_id, operations_json) VALUES (?, ?, ?)",
                (patch_id, paper_id, json.dumps(operations, default=str)),
            )
        return patch_id

    def vector_search(self, paper_id: str, query: str, limit: int = 10) -> list[tuple[str, float]]:
        return []


class PostgresGraphStore(GraphStore):
    def __init__(self, engine: Engine, embeddings: EmbeddingClient | None = None) -> None:
        self._engine = engine
        self._embeddings = embeddings or EmbeddingClient()

    def save_paper(self, parsed: ParsedPaper, graph: PaperArgumentGraph) -> None:
        node_embeddings = self._embed_nodes(graph.nodes)
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO papers (id, title, abstract, metadata_json) "
                    "VALUES (:id, :title, :abstract, CAST(:metadata AS jsonb)) "
                    "ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title, "
                    "abstract = EXCLUDED.abstract, metadata_json = EXCLUDED.metadata_json"
                ),
                {
                    "id": parsed.paper_id,
                    "title": parsed.title,
                    "abstract": parsed.abstract,
                    "metadata": json.dumps(parsed.metadata, default=str),
                },
            )
            for table in ("edges", "nodes", "semantic_units", "paper_references"):
                conn.execute(text(f"DELETE FROM {table} WHERE paper_id = :pid"), {"pid": parsed.paper_id})  # noqa: S608
            for unit in parsed.semantic_units:
                conn.execute(
                    text(
                        "INSERT INTO semantic_units (id, paper_id, role, title, text, source_location_json, "
                        "confidence, created_by, properties_json) VALUES (:id, :pid, :role, :title, :text, "
                        "CAST(:source_location AS jsonb), :confidence, :created_by, CAST(:properties AS jsonb))"
                    ),
                    {
                        "id": unit.semantic_unit_id,
                        "pid": parsed.paper_id,
                        "role": unit.role,
                        "title": unit.title,
                        "text": unit.text,
                        "source_location": json.dumps(unit.source_location.model_dump()),
                        "confidence": unit.confidence,
                        "created_by": unit.created_by,
                        "properties": json.dumps(unit.properties, default=str),
                    },
                )
            self._insert_nodes(conn, graph.nodes, node_embeddings)
            self._insert_edges(conn, graph.edges)
            for reference in parsed.references:
                conn.execute(
                    text(
                        "INSERT INTO paper_references (id, paper_id, raw_text, title, authors, year, doi, arxiv_id, metadata_json) "
                        "VALUES (:id, :pid, :raw_text, :title, :authors, :year, :doi, :arxiv_id, :metadata)"
                    ),
                    {
                        "id": reference.reference_id,
                        "pid": parsed.paper_id,
                        "raw_text": reference.raw_text,
                        "title": reference.title,
                        "authors": json.dumps(reference.authors),
                        "year": reference.year,
                        "doi": reference.doi,
                        "arxiv_id": reference.arxiv_id,
                        "metadata": json.dumps({"marker": reference.marker}),
                    },
                )
    def list_papers(self) -> list[dict]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, title, abstract, metadata_json, created_at "
                    "FROM papers ORDER BY created_at DESC"
                )
            ).mappings()
            return [
                {
                    "paper_id": str(row["id"]),
                    "title": row["title"],
                    "abstract": row["abstract"],
                    "metadata": row["metadata_json"] or {},
                    "created_at": row["created_at"].isoformat(),
                }
                for row in rows
            ]

    def delete_paper(self, paper_id: str) -> bool:
        source_path: str | None = None
        with self._engine.begin() as conn:
            row = conn.execute(
                text("SELECT metadata_json FROM papers WHERE id = :pid"),
                {"pid": paper_id},
            ).mappings().first()
            if row is None:
                return False
            source = (row["metadata_json"] or {}).get("source_document") or {}
            source_path = source.get("path")
            conn.execute(text("DELETE FROM papers WHERE id = :pid"), {"pid": paper_id})

        if source_path:
            path = Path(source_path)
            try:
                if path.exists() and path.is_file():
                    path.unlink()
            except OSError as exc:
                logger.warning("Failed to delete source document for %s: %s", paper_id, exc)
        return True

    def save_source_document(self, paper_id: str, filename: str, media_type: str, data: bytes) -> None:
        directory = Path(settings.document_store_dir).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        suffix = Path(filename).suffix.lower() or ".pdf"
        path = directory / f"{paper_id}{suffix}"
        path.write_bytes(data)
        metadata = {
            "source_document": {
                "filename": filename,
                "media_type": media_type,
                "path": str(path),
                "size": len(data),
            }
        }
        with self._engine.begin() as conn:
            conn.execute(
                text("UPDATE papers SET metadata_json = metadata_json || CAST(:metadata AS jsonb) WHERE id = :pid"),
                {"pid": paper_id, "metadata": json.dumps(metadata, default=str)},
            )

    def get_source_document(self, paper_id: str) -> SourceDocument | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                text("SELECT metadata_json FROM papers WHERE id = :pid"),
                {"pid": paper_id},
            ).mappings().first()
        if not row:
            return None
        source = (row["metadata_json"] or {}).get("source_document") or {}
        path_value = source.get("path")
        if not path_value:
            return None
        path = Path(path_value)
        if not path.exists() or not path.is_file():
            return None
        return SourceDocument(
            filename=source.get("filename") or path.name,
            media_type=source.get("media_type") or "application/octet-stream",
            data=path.read_bytes(),
        )

    def get_graph(self, paper_id: str) -> PaperArgumentGraph | None:
        with self._engine.connect() as conn:
            exists = conn.execute(text("SELECT 1 FROM papers WHERE id = :pid"), {"pid": paper_id}).first()
            if not exists:
                return None
            node_rows = conn.execute(
                text(
                    "SELECT id, node_type, title, summary, properties_json, semantic_unit_ids, page_ranges, "
                    "confidence, source_type, created_by, verified FROM nodes WHERE paper_id = :pid"
                ),
                {"pid": paper_id},
            ).mappings()
            nodes = [
                GraphNode(
                    id=row["id"],
                    paper_id=paper_id,
                    node_type=row["node_type"],
                    title=row["title"],
                    summary=row["summary"],
                    confidence=row["confidence"],
                    source_type=row["source_type"],
                    semantic_unit_ids=list(row["semantic_unit_ids"] or []),
                    page_ranges=[tuple(pair) for pair in row["page_ranges"]],
                    properties=row["properties_json"],
                    created_by=row["created_by"],
                    verified=row["verified"],
                )
                for row in node_rows
            ]
            edge_rows = conn.execute(
                text(
                    "SELECT id, source_node_id, target_node_id, edge_type, semantic_unit_ids, confidence, "
                    "inference_type, properties_json FROM edges WHERE paper_id = :pid"
                ),
                {"pid": paper_id},
            ).mappings()
            edges = [
                GraphEdge(
                    id=row["id"],
                    paper_id=paper_id,
                    source_node_id=row["source_node_id"],
                    target_node_id=row["target_node_id"],
                    edge_type=row["edge_type"],
                    confidence=row["confidence"],
                    semantic_unit_ids=list(row["semantic_unit_ids"] or []),
                    inference_type=row["inference_type"],
                    properties=row["properties_json"],
                )
                for row in edge_rows
            ]
        return PaperArgumentGraph(paper_id=paper_id, nodes=nodes, edges=edges)

    def replace_graph(self, paper_id: str, graph: PaperArgumentGraph) -> None:
        node_embeddings = self._embed_nodes(graph.nodes)
        with self._engine.begin() as conn:
            conn.execute(text("DELETE FROM edges WHERE paper_id = :pid"), {"pid": paper_id})
            conn.execute(text("DELETE FROM nodes WHERE paper_id = :pid"), {"pid": paper_id})
            self._insert_nodes(conn, graph.nodes, node_embeddings)
            self._insert_edges(conn, graph.edges)

    def get_semantic_units(self, paper_id: str) -> list[SemanticUnit]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, role, title, text, source_location_json, confidence, created_by, properties_json "
                    "FROM semantic_units WHERE paper_id = :pid ORDER BY id"
                ),
                {"pid": paper_id},
            ).mappings()
            return [
                SemanticUnit(
                    semantic_unit_id=row["id"],
                    paper_id=paper_id,
                    role=row["role"],
                    title=row["title"],
                    text=row["text"],
                    source_location=PageSourceLocation(**row["source_location_json"]),
                    confidence=row["confidence"],
                    created_by=row["created_by"],
                    properties=row["properties_json"],
                )
                for row in rows
            ]

    def get_references(self, paper_id: str) -> list[PaperReference]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, raw_text, title, authors, year, doi, arxiv_id, metadata_json "
                    "FROM paper_references WHERE paper_id = :pid ORDER BY id"
                ),
                {"pid": paper_id},
            ).mappings()
            return [self._reference_from_row(row) for row in rows]

    def find_reference(self, reference_id: str) -> PaperReference | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT id, raw_text, title, authors, year, doi, arxiv_id, metadata_json "
                    "FROM paper_references WHERE id = :rid"
                ),
                {"rid": reference_id},
            ).mappings().first()
            return self._reference_from_row(row) if row else None

    @staticmethod
    def _reference_from_row(row) -> PaperReference:
        return PaperReference(
            reference_id=row["id"],
            marker=(row["metadata_json"] or {}).get("marker"),
            raw_text=row["raw_text"],
            title=row["title"],
            authors=row["authors"] or [],
            year=row["year"],
            doi=row["doi"],
            arxiv_id=row["arxiv_id"],
        )

    def update_reference(self, reference: PaperReference) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE paper_references SET title = :title, authors = :authors, year = :year, "
                    "doi = :doi, arxiv_id = :arxiv_id WHERE id = :rid"
                ),
                {
                    "rid": reference.reference_id,
                    "title": reference.title,
                    "authors": json.dumps(reference.authors),
                    "year": reference.year,
                    "doi": reference.doi,
                    "arxiv_id": reference.arxiv_id,
                },
            )

    def record_patch(self, paper_id: str, operations: list[dict]) -> str:
        patch_id = str(uuid4())
        with self._engine.begin() as conn:
            conn.execute(
                text("INSERT INTO graph_patches (id, paper_id, operations_json) VALUES (:id, :pid, :ops)"),
                {"id": patch_id, "pid": paper_id, "ops": json.dumps(operations)},
            )
        return patch_id

    def vector_search(self, paper_id: str, query: str, limit: int = 10) -> list[tuple[str, float]]:
        if not self._embeddings.available:
            return []
        vectors = self._embeddings.embed([query])
        if not vectors:
            return []
        query_vector = "[" + ",".join(f"{value:.6f}" for value in vectors[0]) + "]"
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, 1 - (embedding <=> CAST(:qvec AS vector)) AS similarity FROM nodes "
                    "WHERE paper_id = :pid AND embedding IS NOT NULL "
                    "ORDER BY embedding <=> CAST(:qvec AS vector) LIMIT :limit"
                ),
                {"qvec": query_vector, "pid": paper_id, "limit": limit},
            )
            return [(row[0], float(row[1])) for row in rows]

    def _embed_nodes(self, nodes: list[GraphNode]) -> dict[str, str]:
        if not self._embeddings.available or not nodes:
            return {}
        vectors = self._embeddings.embed([f"{node.title}\n{node.summary}" for node in nodes])
        if not vectors:
            return {}
        return {
            node.id: "[" + ",".join(f"{value:.6f}" for value in vector) + "]"
            for node, vector in zip(nodes, vectors)
        }

    @staticmethod
    def _insert_nodes(conn, nodes: list[GraphNode], embeddings: dict[str, str]) -> None:
        for node in nodes:
            conn.execute(
                text(
                    "INSERT INTO nodes (id, paper_id, node_type, title, summary, properties_json, semantic_unit_ids, "
                    "page_ranges, confidence, source_type, created_by, verified, embedding) "
                    "VALUES (:id, :pid, :node_type, :title, :summary, :properties, :semantic_unit_ids, :page_ranges, "
                    ":confidence, :source_type, :created_by, :verified, CAST(:embedding AS vector))"
                ),
                {
                    "id": node.id,
                    "pid": node.paper_id,
                    "node_type": str(node.node_type),
                    "title": node.title,
                    "summary": node.summary,
                    "properties": json.dumps(node.properties, default=str),
                    "semantic_unit_ids": node.semantic_unit_ids,
                    "page_ranges": json.dumps([list(pair) for pair in node.page_ranges]),
                    "confidence": node.confidence,
                    "source_type": node.source_type,
                    "created_by": node.created_by,
                    "verified": node.verified,
                    "embedding": embeddings.get(node.id),
                },
            )

    @staticmethod
    def _insert_edges(conn, edges: list[GraphEdge]) -> None:
        for edge in edges:
            conn.execute(
                text(
                    "INSERT INTO edges (id, paper_id, source_node_id, target_node_id, edge_type, semantic_unit_ids, "
                    "confidence, inference_type, properties_json) VALUES (:id, :pid, :source, :target, :edge_type, "
                    ":semantic_unit_ids, :confidence, :inference_type, :properties)"
                ),
                {
                    "id": edge.id,
                    "pid": edge.paper_id,
                    "source": edge.source_node_id,
                    "target": edge.target_node_id,
                    "edge_type": str(edge.edge_type),
                    "semantic_unit_ids": edge.semantic_unit_ids,
                    "confidence": edge.confidence,
                    "inference_type": edge.inference_type,
                    "properties": json.dumps(edge.properties, default=str),
                },
            )


def create_graph_store() -> GraphStore:
    """Returns a SQLite/Postgres-backed store when configured, else in-memory."""
    if settings.database_url in {"", "memory"}:
        return InMemoryGraphStore()
    if settings.database_url.startswith("sqlite:///"):
        logger.info("Using SQLite graph store at %s", settings.database_url)
        return SQLiteGraphStore(settings.database_url)
    try:
        engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("Using PostgreSQL graph store at %s", engine.url.render_as_string(hide_password=True))
        return PostgresGraphStore(engine)
    except SQLAlchemyError as exc:
        logger.warning("Database unavailable (%s); falling back to in-memory graph store", exc)
        return InMemoryGraphStore()


def _loads_json(value: str | bytes | None, default):
    if value in (None, "", b""):
        return default
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        logger.warning("Failed to parse JSON payload from local store")
        return default
