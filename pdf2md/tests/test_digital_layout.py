"""Page-level behaviours seen on real documents (academic papers, reports)."""

import samples
from pdf2md import Options, convert, digital

try:
    import pymupdf as fitz
except ImportError:
    import fitz


def two_column_page_with_footnote(path):
    doc = fitz.open()
    p = doc.new_page()
    samples.put(p, 72, 60, [("A Study of Things", "b")], size=18)
    # An author list at a heading size is still prose.
    samples.put(p, 72, 90, [("Ada Lovelace, Charles Babbage, Alan Turing, Grace Hopper, Edsger Dijkstra, John", "r")], size=14)
    samples.put(p, 72, 108, [("McCarthy, Barbara Liskov, Donald Knuth, Frances Allen, Tony Hoare, Leslie Lamport", "r")], size=14)
    samples.put(p, 72, 126, [("and Niklaus Wirth, together with many colleagues from several universities", "r")], size=14)
    samples.put(p, 72, 160, [("1. Section", "b")], size=14)
    left = ["The left column opens the argument", "and carries it down the page until", "it stops in the middle of the"]
    for i, t in enumerate(left):
        samples.put(p, 72, 190 + i * 13, [(t, "r")], size=10)
    # small-print footnote at the bottom of the left column
    samples.put(p, 72, 760, [("Permission to copy is granted provided that", "r")], size=7)
    samples.put(p, 72, 769, [("copies are not made for profit or advantage", "r")], size=7)
    right = ["sentence, which the right column finishes.", "Then it goes on normally."]
    for i, t in enumerate(right):
        samples.put(p, 320, 190 + i * 13, [(t, "r")], size=10)
    for i in range(6):
        samples.put(p, 320, 240 + i * 13, [("Filler text keeps the body size the norm.", "r")], size=10)
        samples.put(p, 72, 240 + i * 13, [("More body text on the left column side.", "r")], size=10)
    doc.save(path)
    return path


def test_footnote_moves_aside_and_paragraph_rejoins(tmp_path):
    md = convert(two_column_page_with_footnote(tmp_path / "p.pdf"), None, Options(verbose=0, cache=False)).markdown
    assert "it stops in the middle of the sentence, which the right column finishes." in md
    assert md.rstrip().endswith("Permission to copy is granted provided that copies are not made for profit or advantage")


def test_long_heading_sized_text_is_a_paragraph(tmp_path):
    md = convert(two_column_page_with_footnote(tmp_path / "p.pdf"), None, Options(verbose=0, cache=False)).markdown
    assert md.startswith("# A Study of Things\n\nAda Lovelace, Charles Babbage")
    assert "## 1. Section" in md
    assert "## Ada" not in md and "## McCarthy" not in md


def test_heading_wrapped_across_blocks_is_one_heading():
    style = digital.DocStyle([])
    r = digital.PageRenderer(style)
    frags = [
        digital.Frag("heading", "Trace-based Just-in-Time Type Specialization for Dynamic", bbox=(100, 70, 500, 90), level=1),
        digital.Frag("heading", "Languages", bbox=(250, 92, 350, 112), level=1),
        digital.Frag("para", "Body."),
    ]
    assert r.assemble(frags) == "# Trace-based Just-in-Time Type Specialization for Dynamic Languages\n\nBody."


def test_no_emphasis_on_bare_symbols():
    spans = [
        digital.Span("Gal", (0, 0, 10, 10), 11),
        digital.Span("∗", (10, 0, 12, 8), 8, italic=True, sup=True),
        digital.Span(" {", (12, 0, 16, 10), 9, italic=True),
        digital.Span("gal", (16, 0, 30, 10), 9, mono=True),
        digital.Span("} ", (30, 0, 34, 10), 9, italic=True),
        digital.Span("word", (34, 0, 60, 10), 11, italic=True),
    ]
    assert digital.render_spans(spans) == "Gal<sup>∗</sup> {`gal`} *word*"


def test_numbered_titles_at_the_top_of_every_page_are_kept(tmp_path):
    """Slides and chapter-per-page documents: "Slide 1", "Slide 2" ... are content, not headers."""
    doc = fitz.open()
    for i in range(1, 6):
        p = doc.new_page()
        samples.put(p, 72, 50, [(f"Slide {i}", "b")], size=20)
        samples.put(p, 72, 120, [(f"Content of slide number {i} goes here in body text.", "r")])
        samples.put(p, 72, 820, [(f"Company Confidential {i}", "r")], size=8)
    doc.save(tmp_path / "slides.pdf")
    md = convert(tmp_path / "slides.pdf", None, Options(verbose=0, cache=False)).markdown
    for i in range(1, 6):
        assert f"# Slide {i}\n" in md
    assert "Confidential" not in md
