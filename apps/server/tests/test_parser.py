import fitz

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
    assert parsed.source_blocks
    assert parsed.source_blocks[0].block_id.startswith("p0001-b")
    assert all(block.text for block in parsed.source_blocks)


def test_unique_ids_across_papers(sample_txt):
    first = PdfParser().parse(sample_txt)
    second = PdfParser().parse(sample_txt)

    assert first.paper_id != second.paper_id


def test_parses_dot_numbered_multiline_references():
    references = PdfParser()._parse_reference_entries(
        [
            "22. Previous, A.: An earlier method. In: Example Conference. pp. 1–8 (2020)",
            "23. Jo, Y., Kim, S.J.: Practical single-image super-resolution using look-up",
            "table. In: IEEE Conference on Computer Vision and Pattern Recognition. pp. 691–700 (2021)",
            "24. Next, B.: A later method. IEEE Trans. Example 2 (1), 10–20 (2022)",
        ],
        "paper123",
    )

    assert [reference.marker for reference in references] == ["[22]", "[23]", "[24]"]
    assert references[1].title == (
        "Practical single-image super-resolution using look-up table"
    )
    assert "pp. 691–700" in references[1].raw_text
    assert references[1].year == 2021


def test_reference_year_prefers_trailing_parenthesized_year_over_page_numbers():
    references = PdfParser()._parse_reference_entries(
        [
            "49. Song, Q.: Fast image super-resolution. IEEE Trans. 27(4), 1966–1980 (2018)",
            "50. Timofte, R.: Anchored regression. In: ICCV. pp. 1920–1927 (2013)",
            "51. Xiong, Z.: Robust web super-resolution. IEEE Trans. 19(8), 2017–2028 (2010)",
        ],
        "paper123",
    )

    assert [reference.year for reference in references] == [2018, 2013, 2010]


def test_parses_pdf_plain_text_with_cross_page_paragraph_merge(tmp_path):
    path = tmp_path / "cross-page.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.new_page()
    page1 = doc.load_page(0)
    page2 = doc.load_page(1)
    page1.insert_textbox(
        fitz.Rect(50, 60, 545, 120),
        "CrossPage Title",
        fontsize=18,
        fontname="helvetica-bold",
    )
    page1.insert_textbox(
        fitz.Rect(50, 650, 545, 740),
        "This paragraph starts near the bottom of page one and continues",
        fontsize=10,
        fontname="helvetica",
    )
    page2.insert_textbox(
        fitz.Rect(50, 50, 545, 140),
        "onto page two without ending the sentence.",
        fontsize=10,
        fontname="helvetica",
    )
    page2.insert_textbox(
        fitz.Rect(50, 180, 545, 260),
        "A new paragraph starts here.",
        fontsize=10,
        fontname="helvetica",
    )
    doc.save(path)
    doc.close()

    parsed = PdfParser().parse(path)

    assert (
        "This paragraph starts near the bottom of page one and continues onto page two without ending the sentence."
        in parsed.metadata["plain_text"]
    )
    assert parsed.metadata["plain_text"].count("A new paragraph starts here.") == 1
