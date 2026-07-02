from pathlib import Path

import fitz
import pytest
from fastapi.testclient import TestClient

import understand_anypaper.api.routes as routes
from understand_anypaper.main import app
from understand_anypaper.storage import InMemoryGraphStore

SAMPLE_TEXT = """# LinearAttention: Efficient Transformers

Abstract. We propose a linear-time attention module that addresses the gap left by prior work [1, 2].

Existing transformers fail on long contexts; this limitation motivates our work [1].

Our main contribution is a linear-time attention method described below.

The method extends the attention module of [2] with a gating mechanism.

We run experiments on the LRA dataset and our results outperform baselines [3].

## References

[1] A. Vaswani, N. Shazeer. Attention is all you need. NeurIPS, 2017.
[2] J. Devlin. BERT: Pre-training of deep bidirectional transformers. 2019. arXiv:1810.04805
[3] Y. Tay. Long range arena. ICLR, 2021.
"""


@pytest.fixture()
def client() -> TestClient:
    routes._store = InMemoryGraphStore()
    with TestClient(app) as test_client:
        yield test_client
    routes._store = None


@pytest.fixture()
def sample_txt(tmp_path: Path) -> Path:
    path = tmp_path / "linear-attention.md"
    path.write_text(SAMPLE_TEXT)
    return path


@pytest.fixture()
def sample_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "linear-attention.pdf"
    doc = fitz.open()
    page = doc.new_page()
    y = 60

    def add(text: str, size: int = 10, bold: bool = False) -> None:
        nonlocal y
        fontname = "helvetica-bold" if bold else "helvetica"
        page.insert_textbox(fitz.Rect(50, y, 545, y + 220), text, fontsize=size, fontname=fontname)
        y += 26 + 13 * text.count("\n") + (size - 10) * 3

    add("LinearAttention: Efficient Transformers", size=18, bold=True)
    add("Abstract", size=12, bold=True)
    add("Abstract. We propose a linear-time attention module that addresses the gap left by prior work [1, 2].")
    add("1 Introduction", size=12, bold=True)
    add("Existing transformers fail on long contexts; this limitation motivates our work [1].")
    add("Our main contribution is a linear-time attention method that we propose in Section 3.")
    add("3 Method", size=12, bold=True)
    add("Our method extends the attention module of [2] with a gating mechanism.")
    add("Figure 1: Overview of the proposed architecture.")
    add("4 Experiments", size=12, bold=True)
    add("We run experiments on the LRA dataset and our results outperform baselines [3].")
    add("References", size=12, bold=True)
    add(
        "[1] A. Vaswani, N. Shazeer. Attention is all you need. NeurIPS, 2017.\n"
        "[2] J. Devlin. BERT: Pre-training of deep bidirectional transformers. 2019. arXiv:1810.04805\n"
        "[3] Y. Tay. Long range arena. ICLR, 2021."
    )
    doc.save(path)
    doc.close()
    return path
