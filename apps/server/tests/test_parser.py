from understand_anypaper.parser.pdf_parser import PdfParser


def test_parses_markdown_with_references(sample_txt):
    parsed = PdfParser().parse(sample_txt)

    assert parsed.title == "LinearAttention: Efficient Transformers"
    assert parsed.source_blocks, "expected source blocks"
    assert not parsed.semantic_units
    assert len(parsed.references) == 3
    assert parsed.references[0].year == 2017
    assert parsed.references[1].arxiv_id == "1810.04805"
    markers = {ref.marker for ref in parsed.references}
    assert markers == {"[1]", "[2]", "[3]"}
    assert parsed.mentions, "expected citation mentions"
    mentioned_refs = {mention.reference_id for mention in parsed.mentions}
    assert len(mentioned_refs) == 3


def test_parses_real_pdf_with_pages_and_bboxes(sample_pdf):
    parsed = PdfParser().parse(sample_pdf)

    assert parsed.title == "LinearAttention: Efficient Transformers"
    assert parsed.source_blocks
    assert all(block.page >= 1 for block in parsed.source_blocks)
    assert all(block.bbox and len(block.bbox) == 4 for block in parsed.source_blocks)
    assert not parsed.semantic_units
    assert any(block.block_type == "figure_caption" for block in parsed.source_blocks)
    assert len(parsed.references) == 3
    assert parsed.mentions


def test_unique_ids_across_papers(sample_txt):
    first = PdfParser().parse(sample_txt)
    second = PdfParser().parse(sample_txt)

    first_ids = {block.source_block_id for block in first.source_blocks}
    second_ids = {block.source_block_id for block in second.source_blocks}
    assert not first_ids & second_ids
