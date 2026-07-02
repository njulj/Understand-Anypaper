from pathlib import Path
from uuid import uuid4

from understand_anypaper.parser.models import ContentBlock, ParsedPaper


class PdfParser:
    """MVP parser facade.

    Production parsing will plug in layout, equation, figure, table, and reference
    extractors. For now, text-like inputs and uploaded filenames create stable
    content atoms so the rest of the PAG pipeline is runnable.
    """

    def parse(self, path: Path) -> ParsedPaper:
        text = path.read_text(errors="ignore") if path.suffix.lower() in {".txt", ".md"} else ""
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()] or [path.name]
        blocks = [
            ContentBlock(
                content_id=f"text-page1-block{i}",
                order=i,
                page=1,
                text=paragraph,
                semantic_role=self._classify_role(paragraph),
            )
            for i, paragraph in enumerate(paragraphs, start=1)
        ]
        return ParsedPaper(
            paper_id=str(uuid4()),
            title=path.stem,
            abstract=paragraphs[0][:1000] if paragraphs else "",
            blocks=blocks,
        )

    @staticmethod
    def _classify_role(text: str) -> str:
        lower = text.lower()
        if "contribution" in lower or "we propose" in lower:
            return "contribution"
        if "limitation" in lower or "gap" in lower:
            return "gap"
        if "method" in lower or "module" in lower:
            return "method"
        if "experiment" in lower or "ablation" in lower:
            return "experiment"
        if "result" in lower or "improve" in lower:
            return "result"
        if "conclusion" in lower:
            return "conclusion"
        return "background"
