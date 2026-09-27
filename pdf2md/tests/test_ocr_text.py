"""Turning OCR output into Markdown: Tesseract TSV and running-header removal."""

from pdf2md.converter import PageResult, strip_repeated_edges
from pdf2md.ocr.tesseract import tsv_to_markdown

HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


def tsv(lines):
    """lines: [(block, par, line, left, top, [(word, width)], height)]"""
    rows = [HEADER]
    for block, par, line, left, top, words, height in lines:
        x = left
        for i, (w, width) in enumerate(words, 1):
            rows.append(f"5\t1\t{block}\t{par}\t{line}\t{i}\t{x}\t{top}\t{width}\t{height}\t95\t{w}")
            x += width + 12
    return "\n".join(rows)


def words(text, cw=18):
    return [(w, cw * len(w)) for w in text.split()]


def test_paragraphs_come_from_spacing_not_tesseract_numbering():
    data = tsv([
        (1, 1, 1, 100, 100, words("Introduction"), 30),
        (1, 1, 2, 100, 200, words("The first paragraph starts here and it"), 30),
        (1, 1, 3, 100, 245, words("continues on the next line."), 30),
        (1, 2, 1, 100, 290, words("Still the same paragraph despite a new"), 30),
        (1, 2, 2, 100, 335, words("Tesseract paragraph number."), 30),
    ])
    md, conf = tsv_to_markdown(data)
    # One paragraph despite Tesseract's numbering; the short line that ends a
    # sentence keeps its line break (a new statement usually starts there).
    assert md == (
        "Introduction\n\n"
        "The first paragraph starts here and it continues on the next line.\n"
        "Still the same paragraph despite a new Tesseract paragraph number.\n"
    )
    assert conf == 95


def test_tall_narrow_junk_and_stray_symbols_are_dropped():
    data = tsv([
        (1, 1, 1, 100, 100, words("Real text on a line of the page."), 30),
        (2, 1, 1, 40, 100, [("DnuBWNPR", 30)], 400),
        (3, 1, 1, 100, 160, [("|", 6)], 30),
    ])
    md, _ = tsv_to_markdown(data)
    assert md == "Real text on a line of the page.\n"


def test_bulleted_lines_become_a_list():
    data = tsv([
        (1, 1, 1, 100, 100, words("• first point"), 30),
        (1, 1, 2, 100, 145, words("• second point that wraps"), 30),
        (1, 1, 3, 130, 190, words("onto another line"), 30),
    ])
    md, _ = tsv_to_markdown(data)
    assert md == "- first point\n- second point that wraps onto another line\n"


def page(n, md, method="vlm"):
    return PageResult(n, "scanned", method, md)


def test_running_headers_removed_but_changing_dates_kept():
    pages = [
        page(i, f"Journal of Things - Vol 3\n\nDate: {10 + i} March\n\nBody of page {i}.\n\n{i}", "tesseract")
        for i in range(1, 6)
    ]
    strip_repeated_edges(pages)
    for i, p in enumerate(pages, 1):
        assert p.markdown == f"Date: {10 + i} March\n\nBody of page {i}.\n"


def test_model_output_keeps_a_lone_number():
    pages = [page(i, f"Header text\n\nWorking for problem {i}.\n\n{40 + i}") for i in range(1, 6)]
    strip_repeated_edges(pages)
    assert pages[0].markdown == "Working for problem 1.\n\n41\n"


def test_few_pages_are_left_alone():
    pages = [page(i, "Same header\n\nText.") for i in range(1, 4)]
    strip_repeated_edges(pages)
    assert pages[0].markdown == "Same header\n\nText."
