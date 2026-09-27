import io

import pytest
from PIL import Image

import samples
from conftest import PRINTED_LINES, needs_tesseract
from pdf2md import Options, convert
from pdf2md.cli import main
from pdf2md.converter import PasswordRequired

try:
    import pymupdf as fitz
except ImportError:
    import fitz


def vlm_options(server, tmp_path, **kw):
    kw.setdefault("cache_dir", tmp_path / "cache")
    return Options(engine="vlm", base_url=server.url, verbose=0, **kw)


def mixed_pdf(tmp_path, digital_pdf, scanned_pdf):
    """Digital pages 1-3 followed by a scanned page 4."""
    doc = fitz.open(digital_pdf)
    doc.insert_pdf(fitz.open(scanned_pdf))
    path = tmp_path / "mixed.pdf"
    doc.save(path)
    return path


def test_scanned_page_goes_to_the_model(model_server, scanned_pdf, tmp_path):
    model_server.reply = lambda req, path: "# Chapter 4\n\nPlants convert light."
    res = convert(scanned_pdf, tmp_path / "s.md", vlm_options(model_server, tmp_path))
    assert res.markdown == "# Chapter 4\n\nPlants convert light.\n"
    assert (tmp_path / "s.md").read_text(encoding="utf-8") == res.markdown
    assert [p.method for p in res.pages] == ["vlm"]
    assert not res.incomplete


def test_ocr_results_are_cached(model_server, scanned_pdf, tmp_path):
    opts = vlm_options(model_server, tmp_path)
    first = convert(scanned_pdf, None, opts)
    second = convert(scanned_pdf, None, opts)
    assert len(model_server.requests) == 1
    assert second.markdown == first.markdown
    assert second.pages[0].cached
    # A different model is a different result.
    model_server.models.append("minicpm-v:8b")
    convert(scanned_pdf, None, vlm_options(model_server, tmp_path, model="minicpm-v:8b"))
    assert len(model_server.requests) == 2
    convert(scanned_pdf, None, vlm_options(model_server, tmp_path, cache=False))
    assert len(model_server.requests) == 3


def test_digital_and_scanned_pages_in_order(model_server, digital_pdf, scanned_pdf, tmp_path):
    model_server.reply = lambda req, path: "SCANNED PAGE TEXT."
    path = mixed_pdf(tmp_path, digital_pdf, scanned_pdf)
    res = convert(path, tmp_path / "m.md", vlm_options(model_server, tmp_path))
    assert [p.method for p in res.pages] == ["text", "text", "text", "vlm"]
    assert len(model_server.requests) == 1
    md = res.markdown
    assert md.index("Quarterly Notes") < md.index("Two Columns") < md.index("SCANNED PAGE TEXT.")


def test_no_engine_leaves_a_marker(digital_pdf, scanned_pdf, tmp_path):
    path = mixed_pdf(tmp_path, digital_pdf, scanned_pdf)
    res = convert(path, None, Options(engine="none", verbose=0, cache=False))
    assert res.incomplete
    assert "<!-- pdf2md: page 4 is a scanned or handwritten page" in res.markdown
    assert "Quarterly Notes" in res.markdown
    assert any("not transcribed" in w for w in res.warnings)


def test_blank_scan_is_not_sent_to_the_model(model_server, tmp_path):
    path = samples.image_pdf(tmp_path / "blank.pdf", [Image.new("L", (1654, 2339), 255)])
    res = convert(path, None, vlm_options(model_server, tmp_path))
    assert res.pages[0].method == "blank"
    assert model_server.requests == []


def test_mode_ocr_reads_every_page_from_its_image(model_server, digital_pdf, tmp_path):
    res = convert(digital_pdf, None, vlm_options(model_server, tmp_path, mode="ocr"))
    assert [p.method for p in res.pages] == ["vlm"] * 3
    assert len(model_server.requests) == 3


def test_ocr_pages_option(model_server, digital_pdf, tmp_path):
    res = convert(digital_pdf, None, vlm_options(model_server, tmp_path, ocr_pages="2"))
    assert [p.method for p in res.pages] == ["text", "vlm", "text"]


def test_mode_digital_never_calls_a_model(model_server, digital_pdf, scanned_pdf, tmp_path):
    path = mixed_pdf(tmp_path, digital_pdf, scanned_pdf)
    res = convert(path, None, vlm_options(model_server, tmp_path, mode="digital"))
    assert model_server.requests == []
    assert res.pages[3].method == "missing"


def test_page_selection_and_markers(digital_pdf, tmp_path):
    res = convert(digital_pdf, None, Options(pages="2-3", page_markers=True, verbose=0, cache=False))
    assert res.markdown.startswith("<!-- page 2 -->\n\n## 2. Two Columns")
    assert "<!-- page 3 -->" in res.markdown
    assert "Quarterly Notes" not in res.markdown


def test_handwriting_on_a_typed_page_goes_to_the_model(model_server, tmp_path):
    path = samples.ink_pdf(tmp_path / "ink.pdf")
    doc = fitz.open(path)
    doc[0].insert_text((72, 700), "A typed heading above some handwritten working.", fontsize=11)
    doc.save(tmp_path / "annotated.pdf")
    model_server.reply = lambda req, path: "A typed heading above some handwritten working.\n\nx = 2y + 3"
    res = convert(tmp_path / "annotated.pdf", None, vlm_options(model_server, tmp_path))
    assert res.pages[0].kind == "mixed" and res.pages[0].method == "vlm"
    assert "x = 2y + 3" in res.markdown


def test_photo_of_notes_inside_a_typed_page(model_server, tmp_path):
    """A photo of handwritten notes pasted into a document with a typed title."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 60), "Lecture 3: The first law of thermodynamics", fontsize=16)
    photo = samples.text_image(["Handwritten photo content"], size=60, width=1200, height=1500)
    buf = io.BytesIO()
    photo.convert("RGB").save(buf, "JPEG")
    page.insert_image(fitz.Rect(72, 80, 520, 640), stream=buf.getvalue())
    doc.save(tmp_path / "photo.pdf")
    model_server.reply = lambda req, path: "Handwritten photo content"
    res = convert(tmp_path / "photo.pdf", tmp_path / "photo.md", vlm_options(model_server, tmp_path))
    md = res.markdown
    assert md.index("Lecture 3") < md.index("![Page 1, image 1]") < md.index("Handwritten photo content")
    assert res.pages[0].method == "text"


def test_image_file_input(model_server, tmp_path):
    img = samples.text_image(["A photographed page"], size=50, width=1000, height=1400)
    img.convert("RGB").save(tmp_path / "photo.jpg")
    model_server.reply = lambda req, path: "A photographed page"
    res = convert(tmp_path / "photo.jpg", None, vlm_options(model_server, tmp_path))
    assert res.markdown == "A photographed page\n"


def test_password_protected(digital_pdf, tmp_path):
    doc = fitz.open(digital_pdf)
    locked = tmp_path / "locked.pdf"
    doc.save(locked, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="s3cret", owner_pw="owner")
    with pytest.raises(PasswordRequired, match="--password"):
        convert(locked, None, Options(verbose=0, cache=False))
    with pytest.raises(PasswordRequired, match="wrong password"):
        convert(locked, None, Options(verbose=0, cache=False, password="nope"))
    res = convert(locked, None, Options(verbose=0, cache=False, password="s3cret"))
    assert "Quarterly Notes" in res.markdown


def test_old_images_are_replaced_not_accumulated(digital_pdf, tmp_path):
    out = tmp_path / "d.md"
    assets = tmp_path / "d_assets"
    assets.mkdir()
    (assets / "page009-img1.png").write_bytes(b"stale")
    (assets / "my-own-file.txt").write_text("keep me")
    convert(digital_pdf, out, Options(verbose=0, cache=False))
    names = sorted(p.name for p in assets.iterdir())
    assert names == ["my-own-file.txt", "page003-fig1.png", "page003-img1.png"]


@needs_tesseract
def test_tesseract_reads_a_printed_scan(scanned_pdf, tmp_path):
    res = convert(scanned_pdf, None, Options(engine="tesseract", verbose=0, cache=False))
    md = res.markdown
    assert res.pages[0].method == "tesseract"
    assert md.startswith("Chapter 4: Photosynthesis\n\n")
    assert "Plants convert light energy into chemical energy. The process takes place in the chloroplasts" in md
    assert "- Light reactions happen in the thylakoids\n- The Calvin cycle fixes carbon dioxide" in md


@needs_tesseract
def test_tesseract_missing_language_is_explained():
    from pdf2md.ocr.base import EngineUnavailable
    from pdf2md.ocr.tesseract import TesseractEngine

    with pytest.raises(EngineUnavailable, match="tesseract-ocr-zzz"):
        TesseractEngine(lang="eng+zzz").check()


# ------------------------------------------------------------------ CLI


def test_cli_writes_markdown_next_to_the_pdf(digital_pdf, tmp_path):
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(digital_pdf.read_bytes())
    assert main([str(pdf), "-q", "--no-cache"]) == 0
    assert (tmp_path / "report.md").read_text(encoding="utf-8").startswith("# Quarterly Notes")
    assert (tmp_path / "report_assets" / "page003-img1.png").exists()


def test_cli_stdout(digital_pdf, capsys):
    assert main([str(digital_pdf), "-o", "-", "-q", "--no-cache"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Quarterly Notes") and "![" not in out


def test_cli_exit_code_when_pages_are_missing(scanned_pdf, tmp_path):
    assert main([str(scanned_pdf), "-o", str(tmp_path / "x.md"), "--engine", "none", "-q"]) == 3


def test_cli_explicit_engine_that_is_not_running(scanned_pdf, tmp_path, capsys):
    code = main([str(scanned_pdf), "-o", str(tmp_path / "x.md"), "--engine", "vlm", "--base-url", "http://127.0.0.1:9", "-q"])
    assert code == 1
    assert "ollama serve" in capsys.readouterr().err


def test_cli_batch_folder(model_server, digital_pdf, scanned_pdf, tmp_path):
    src = tmp_path / "in"
    (src / "sub").mkdir(parents=True)
    (src / "a.pdf").write_bytes(digital_pdf.read_bytes())
    (src / "sub" / "b.pdf").write_bytes(scanned_pdf.read_bytes())
    out = tmp_path / "out"
    args = [str(src), "-o", str(out), "-r", "-q", "--engine", "vlm", "--base-url", model_server.url,
            "--cache-dir", str(tmp_path / "cache")]
    assert main(args) == 0
    assert (out / "a.md").exists() and (out / "sub" / "b.md").exists()
    assert main(args + ["--skip-existing"]) == 0
    assert len(model_server.requests) == 1


def test_cli_check(model_server, capsys):
    assert main(["--check", "--base-url", model_server.url]) == 0
    out = capsys.readouterr().out
    assert "[ok] vision model" in out and "Ready for everything" in out


def test_cli_check_without_model(capsys):
    main(["--check", "--base-url", "http://127.0.0.1:9"])
    out = capsys.readouterr().out
    assert "ollama pull qwen3-vl:8b-instruct" in out


def test_a_broken_server_answer_fails_the_page_not_the_run(model_server, digital_pdf, scanned_pdf, tmp_path):
    model_server.broken = True
    path = mixed_pdf(tmp_path, digital_pdf, scanned_pdf)
    res = convert(path, None, vlm_options(model_server, tmp_path))
    assert [p.method for p in res.pages] == ["text", "text", "text", "error"]
    assert res.incomplete
    assert "<!-- pdf2md: page 4 could not be transcribed -->" in res.markdown
    assert any("unreadable answer" in w for w in res.warnings)


def test_phone_photo_rotation_is_honoured(tmp_path):
    from pdf2md.converter import open_document

    img = Image.new("RGB", (400, 200), "white")
    exif = img.getexif()
    exif[0x0112] = 6  # "rotate 90° clockwise to display"
    img.save(tmp_path / "rotated.jpg", exif=exif)
    doc = open_document(tmp_path / "rotated.jpg")
    rect = doc[0].rect
    assert rect.height > rect.width
