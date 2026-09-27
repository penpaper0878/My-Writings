import samples
from pdf2md.classify import profile_page

try:
    import pymupdf as fitz
except ImportError:
    import fitz


def kinds(path):
    with fitz.open(path) as doc:
        return [profile_page(p).kind for p in doc]


def test_digital_pages(digital_pdf):
    assert kinds(digital_pdf) == ["digital", "digital", "digital"]


def test_scanned_page(scanned_pdf):
    assert kinds(scanned_pdf) == ["scanned"]


def test_hidden_ocr_layer_is_ignored(tmp_path):
    img = samples.text_image(["Some handwriting"], size=40)
    path = samples.image_pdf(tmp_path / "s.pdf", [img], invisible_text="garbled scanner ocr text layer here")
    with fitz.open(path) as doc:
        prof = profile_page(doc[0])
    assert prof.kind == "scanned"
    assert prof.invisible_chars > 20 and prof.visible_chars == 0


def test_vector_ink_is_read_as_handwriting(tmp_path):
    assert kinds(samples.ink_pdf(tmp_path / "ink.pdf")) == ["scanned"]


def test_typed_text_with_ink_is_mixed(tmp_path):
    path = samples.ink_pdf(tmp_path / "ink.pdf")
    doc = fitz.open(path)
    doc[0].insert_text((72, 700), "A typed heading above some handwritten working.", fontsize=11)
    out = tmp_path / "mixed.pdf"
    doc.save(out)
    assert kinds(out) == ["mixed"]


def test_blank_page(tmp_path):
    doc = fitz.open()
    doc.new_page()
    doc.save(tmp_path / "blank.pdf")
    assert kinds(tmp_path / "blank.pdf") == ["empty"]


def test_ink_annotation_on_a_typed_page_is_mixed(tmp_path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Typed lecture handout with a student's pen annotation.", fontsize=11)
    page.add_ink_annot([[(100, 200), (120, 210), (140, 205), (160, 220)]])
    doc.save(tmp_path / "annotated.pdf")
    assert kinds(tmp_path / "annotated.pdf") == ["mixed"]


def test_outlined_glyphs_in_a_figure_are_not_handwriting(tmp_path):
    """Filled, curve-heavy paths are usually text converted to outlines, not ink."""
    doc = fitz.open()
    page = doc.new_page()
    for i in range(30):
        page.insert_text((72, 60 + i * 12), f"Line {i} of an ordinary typed page with plenty of real text.", fontsize=10)
    for k in range(40):
        shape = page.new_shape()
        x, y = 80 + (k % 10) * 45, 450 + (k // 10) * 40
        pts = [fitz.Point(x + j * 2, y + (j % 3) * 3) for j in range(31)]
        for j in range(0, 30, 3):
            shape.draw_bezier(pts[j], pts[j + 1], pts[j + 2], pts[j + 3])
        shape.finish(color=None, fill=(0, 0, 0), closePath=True)
        shape.commit()
    doc.save(tmp_path / "outlines.pdf")
    assert kinds(tmp_path / "outlines.pdf") == ["digital"]
