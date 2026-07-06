import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from understand_anypaper.config import settings
from understand_anypaper.graph.schema import GraphEdge, GraphNode, PaperArgumentGraph
from understand_anypaper.parser.models import CitationMention, PageSourceLocation, PaperReference, ParsedPaper, SemanticUnit
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
    def get_mentions(self, reference_id: str) -> list[CitationMention]: ...

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

    def get_mentions(self, reference_id: str) -> list[CitationMention]:
        for paper in self._papers.values():
            mentions = [m for m in paper.mentions if m.reference_id == reference_id]
            if mentions:
                return mentions
        return []

    def record_patch(self, paper_id: str, operations: list[dict]) -> str:
        patch_id = str(uuid4())
        self._patches.setdefault(paper_id, []).append({"id": patch_id, "operations": operations})
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

    @staticmethod
    def _block_number(content_id: str) -> int:
        tail = content_id.rsplit("block", 1)[-1]
        return int(tail) if tail.isdigit() else 0

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

    def get_mentions(self, reference_id: str) -> list[CitationMention]:
        return []

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
    """Returns a Postgres-backed store when the database is reachable, else in-memory."""
    if settings.database_url in {"", "memory"}:
        return InMemoryGraphStore()
    try:
        engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("Using PostgreSQL graph store at %s", engine.url.render_as_string(hide_password=True))
        return PostgresGraphStore(engine)
    except SQLAlchemyError as exc:
        logger.warning("Database unavailable (%s); falling back to in-memory graph store", exc)
        return InMemoryGraphStore()
