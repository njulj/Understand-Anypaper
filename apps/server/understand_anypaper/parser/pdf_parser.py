import re
from collections import Counter
from pathlib import Path
from uuid import uuid4

import fitz  # PyMuPDF

from understand_anypaper.parser.models import DocumentPage, PaperReference, ParsedPaper

_REFERENCE_SECTION = re.compile(r"^\s*(references|bibliography)\s*$", re.IGNORECASE)
_NUMERIC_CITATION = re.compile(r"\[(\d+(?:\s*[,;\-–]\s*\d+)*)\]")
_REF_MARKER = re.compile(r"^\s*\[(\d+)\]\s*")
_CAPTION = re.compile(r"^(figure|fig\.|table)\s*\d+", re.IGNORECASE)
_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_DOI = re.compile(r"\b10\.\d{4,9}/[^\s,;]+", re.IGNORECASE)
_ARXIV = re.compile(r"arxiv[:\s]*(\d{4}\.\d{4,5})", re.IGNORECASE)
_MATH_CHARS = set("=+−-*/^_∑∏∫√∂∇≈≠≤≥∈∀∃αβγδεζηθλμπσφψωΔΣΠΩ()|{}")


class PdfParser:
    """Parses papers into page images plus lightweight metadata.

    For PDFs the LLM receives rendered page images and returns page/bbox evidence.
    PyMuPDF text extraction remains only for metadata and post-LLM bbox text clips.
    """

    def parse(self, path: Path) -> ParsedPaper:
        if path.suffix.lower() == ".pdf":
            return self._parse_pdf(path)
        return self._parse_text(path)

    # ------------------------------------------------------------------ PDF

    def _parse_pdf(self, path: Path) -> ParsedPaper:
        paper_id = str(uuid4())
        prefix = paper_id[:8]
        source_bytes = path.read_bytes()
        doc = fitz.open(path)
        try:
            raw_blocks = self._extract_raw_blocks(doc)
            title = self._detect_title(doc, raw_blocks)
            pages = self._render_pages(doc)
        finally:
            doc.close()

        body_size = self._body_font_size(raw_blocks)
        reference_lines: list[str] = []
        in_references = False
        body_texts: list[str] = []

        for raw in raw_blocks:
            text = raw["text"].strip()
            if not text:
                continue
            is_heading = self._is_heading(raw, body_size)
            if is_heading:
                in_references = bool(_REFERENCE_SECTION.match(text))
                continue
            if in_references:
                reference_lines.append(text)
                continue
            body_texts.append(re.sub(r"\s+", " ", text))

        abstract = self._detect_abstract(body_texts)
        references = self._parse_reference_entries(reference_lines, prefix)
        return ParsedPaper(
            paper_id=paper_id,
            title=title or path.stem,
            abstract=abstract,
            pages=pages,
            references=references,
            source_bytes=source_bytes,
            source_media_type="application/pdf",
        )

    @staticmethod
    def _render_pages(doc: fitz.Document, scale: float = 1.6) -> list[DocumentPage]:
        pages: list[DocumentPage] = []
        matrix = fitz.Matrix(scale, scale)
        for index, page in enumerate(doc, start=1):
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            pages.append(
                DocumentPage(
                    page=index,
                    width=page.rect.width,
                    height=page.rect.height,
                    image_width=pixmap.width,
                    image_height=pixmap.height,
                    image_data=pixmap.tobytes("png"),
                )
            )
        return pages

    @staticmethod
    def _extract_raw_blocks(doc: fitz.Document) -> list[dict]:
        raw_blocks: list[dict] = []
        for page_index, page in enumerate(doc, start=1):
            page_blocks: list[dict] = []
            for block in page.get_text("dict")["blocks"]:
                if block.get("type") != 0:
                    continue
                spans = [span for line in block.get("lines", []) for span in line.get("spans", [])]
                if not spans:
                    continue
                text = "\n".join(
                    " ".join(span["text"] for span in line.get("spans", [])).strip()
                    for line in block.get("lines", [])
                ).strip()
                sizes = [round(span["size"], 1) for span in spans]
                page_blocks.append(
                    {
                        "page": page_index,
                        "bbox": block["bbox"],
                        "text": text,
                        "max_size": max(sizes),
                        "mode_size": Counter(sizes).most_common(1)[0][0],
                        "bold": all(span.get("flags", 0) & 16 for span in spans),
                    }
                )
            raw_blocks.extend(PdfParser._sort_page_blocks(page_blocks, page.rect.width, page.rect.height))
        return raw_blocks

    @staticmethod
    def _sort_page_blocks(blocks: list[dict], page_width: float, page_height: float) -> list[dict]:
        if len(blocks) < 4:
            return sorted(blocks, key=lambda raw: (raw["bbox"][1], raw["bbox"][0]))

        mid_x = page_width / 2
        narrow_blocks = [
            raw
            for raw in blocks
            if (raw["bbox"][2] - raw["bbox"][0]) < page_width * 0.72
        ]
        left = [raw for raw in narrow_blocks if (raw["bbox"][0] + raw["bbox"][2]) / 2 < mid_x]
        right = [raw for raw in narrow_blocks if (raw["bbox"][0] + raw["bbox"][2]) / 2 >= mid_x]
        two_column = len(left) >= 2 and len(right) >= 2
        if not two_column:
            return sorted(blocks, key=lambda raw: (raw["bbox"][1], raw["bbox"][0]))

        def key(raw: dict) -> tuple[int, float, float]:
            x0, y0, x1, _ = raw["bbox"]
            width = x1 - x0
            center_x = (x0 + x1) / 2
            is_wide = width >= page_width * 0.72
            if is_wide and y0 < page_height * 0.25:
                return (-1, y0, x0)
            if is_wide:
                return (2, y0, x0)
            return (0 if center_x < mid_x else 1, y0, x0)

        return sorted(blocks, key=key)

    @staticmethod
    def _body_font_size(raw_blocks: list[dict]) -> float:
        sizes = Counter(raw["mode_size"] for raw in raw_blocks if len(raw["text"]) > 120)
        if not sizes:
            sizes = Counter(raw["mode_size"] for raw in raw_blocks)
        return sizes.most_common(1)[0][0] if sizes else 10.0

    @staticmethod
    def _detect_title(doc: fitz.Document, raw_blocks: list[dict]) -> str:
        meta_title = (doc.metadata or {}).get("title", "").strip()
        if meta_title:
            return meta_title
        first_page = [raw for raw in raw_blocks if raw["page"] == 1 and len(raw["text"]) > 8]
        if not first_page:
            return ""
        return max(first_page, key=lambda raw: raw["max_size"])["text"]

    @staticmethod
    def _is_heading(raw: dict, body_size: float) -> bool:
        text = raw["text"]
        if len(text) > 90 or "\n" in text:
            return False
        if _REFERENCE_SECTION.match(text):
            return True
        looks_bigger = raw["mode_size"] >= body_size * 1.12
        numbered = bool(re.match(r"^\d+(\.\d+)*\.?\s+[A-Z]", text))
        return (looks_bigger and (raw["bold"] or numbered or text.istitle() or text.isupper())) or (
            raw["bold"] and numbered
        )

    @staticmethod
    def _block_type(text: str, raw: dict, body_size: float) -> str:
        if _CAPTION.match(text):
            return "figure_caption" if text.lower().startswith(("figure", "fig.")) else "table_caption"
        stripped = text.replace(" ", "")
        if stripped:
            math_ratio = sum(1 for ch in stripped if ch in _MATH_CHARS or ch.isdigit()) / len(stripped)
            if math_ratio > 0.45 and len(stripped) < 200:
                return "equation"
        return "paragraph"

    @staticmethod
    def _detect_abstract(texts: list[str]) -> str:
        for text in texts:
            if text.lower().startswith("abstract"):
                return text[len("abstract"):].lstrip(" .:—-")[:2000]
        return texts[0][:1000] if texts else ""

    # ----------------------------------------------------------- text / md

    def _parse_text(self, path: Path) -> ParsedPaper:
        paper_id = str(uuid4())
        prefix = paper_id[:8]
        text = path.read_text(errors="ignore")
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()] or [path.name]

        body_paragraphs: list[str] = []
        reference_lines: list[str] = []
        in_references = False
        title = path.stem

        for paragraph in paragraphs:
            heading = re.match(r"^#{1,6}\s+(.+)$", paragraph)
            if heading:
                heading_text = heading.group(1).strip()
                in_references = bool(_REFERENCE_SECTION.match(heading_text))
                if title == path.stem and paragraph.startswith("# "):
                    title = heading_text
                continue
            if _REFERENCE_SECTION.match(paragraph):
                in_references = True
                continue
            if in_references:
                reference_lines.extend(line for line in paragraph.splitlines() if line.strip())
                continue
            body_paragraphs.append(paragraph)

        references = self._parse_reference_entries(reference_lines, prefix)
        return ParsedPaper(
            paper_id=paper_id,
            title=title,
            abstract=body_paragraphs[0][:1000] if body_paragraphs else "",
            pages=[DocumentPage(page=1, width=1, height=1)],
            references=references,
            metadata={"plain_text": "\n\n".join(body_paragraphs)},
            source_bytes=text.encode(),
            source_media_type="text/plain",
        )

    # ----------------------------------------------------------- references

    def _parse_reference_entries(self, lines: list[str], prefix: str) -> list[PaperReference]:
        text = "\n".join(lines).strip()
        if not text:
            return []
        entries: list[tuple[str | None, str]] = []
        markers = list(re.finditer(r"\[(\d+)\]", text))
        # Entry markers in a reference list form an increasing sequence; keeping
        # only those filters out inline citations inside an entry.
        starts: list[re.Match[str]] = []
        for match in markers:
            if not starts or int(match.group(1)) == int(starts[-1].group(1)) + 1:
                starts.append(match)
        if starts:
            for i, match in enumerate(starts):
                end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
                entries.append((match.group(1), text[match.end():end].strip()))
        else:
            entries = [(None, line.strip()) for line in text.splitlines() if len(line.strip()) > 20]
        return [
            self._build_reference(index, marker, raw, prefix)
            for index, (marker, raw) in enumerate(entries, start=1)
        ]

    @staticmethod
    def _build_reference(index: int, marker: str | None, raw: str, prefix: str) -> PaperReference:
        raw = re.sub(r"\s+", " ", raw).strip()
        year_match = _YEAR.search(raw)
        doi_match = _DOI.search(raw)
        arxiv_match = _ARXIV.search(raw)
        title = None
        quoted = re.search(r"[“\"](.+?)[”\"]", raw)
        if quoted:
            title = quoted.group(1).strip(" .,")
        else:
            parts = [part.strip() for part in raw.split(". ") if len(part.strip()) > 12]
            title = next((part.strip(" .,") for part in parts if not _YEAR.search(part)), None)
        authors: list[str] = []
        author_part = raw.split(title, 1)[0] if title and title in raw else raw.split(". ")[0]
        author_part = author_part.strip(" .,")
        if 0 < len(author_part) < 160 and not _YEAR.search(author_part):
            authors = [a.strip(" .") for a in re.split(r",| and ", author_part) if len(a.strip()) > 2][:8]
        return PaperReference(
            reference_id=f"ref-{prefix}-{marker or index}",
            marker=f"[{marker}]" if marker else None,
            raw_text=raw,
            title=title,
            authors=authors,
            year=int(year_match.group(0)) if year_match else None,
            doi=doi_match.group(0).rstrip(".") if doi_match else None,
            arxiv_id=arxiv_match.group(1) if arxiv_match else None,
        )

    @staticmethod
    def _expand_numbers(group: str) -> list[int]:
        numbers: list[int] = []
        for part in re.split(r"[,;]", group):
            part = part.strip()
            range_match = re.match(r"^(\d+)\s*[\-–]\s*(\d+)$", part)
            if range_match:
                start, end = int(range_match.group(1)), int(range_match.group(2))
                if 0 < end - start <= 30:
                    numbers.extend(range(start, end + 1))
            elif part.isdigit():
                numbers.append(int(part))
        return numbers
