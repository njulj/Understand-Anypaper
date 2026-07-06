from understand_anypaper.parser.pdf_parser import PdfParser


def test_parses_markdown_with_references(sample_txt):
    parsed = PdfParser().parse(sample_txt)

    assert parsed.title == "LinearAttention: Efficient Transformers"
    assert parsed.pages
    assert not parsed.semantic_units
    assert len(parsed.references) == 3
    assert parsed.references[0].year == 2017
    assert parsed.references[1].arxiv_id == "1810.04805"
    markers = {ref.marker for ref in parsed.references}
    assert markers == {"[1]", "[2]", "[3]"}
    assert parsed.metadata["plain_text"]


def test_parses_real_pdf_with_page_images(sample_pdf):
    parsed = PdfParser().parse(sample_pdf)

    assert parsed.title == "LinearAttention: Efficient Transformers"
    assert parsed.pages
    assert all(page.page >= 1 for page in parsed.pages)
    assert all(page.width > 0 and page.height > 0 for page in parsed.pages)
    assert all(page.image_data for page in parsed.pages)
    assert not parsed.semantic_units
    assert len(parsed.references) == 3
    assert parsed.source_bytes
    assert parsed.source_media_type == "application/pdf"


def test_unique_ids_across_papers(sample_txt):
    first = PdfParser().parse(sample_txt)
    second = PdfParser().parse(sample_txt)

    assert first.paper_id != second.paper_id
