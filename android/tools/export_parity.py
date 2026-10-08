"""Export parity fixtures: what the desktop tool extracts from a PDF, and the Markdown it makes.

The Android port must turn the same page data into the same Markdown. Run from
the repository root:

    python android/tools/export_parity.py android/core/src/test/resources/parity

Extra PDFs can be given with --pdf (their fixtures are written but should not
be committed if the PDF is not ours to share).
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pdf2md"))
sys.path.insert(0, str(ROOT / "pdf2md" / "tests"))

import pymupdf as fitz  # noqa: E402

from pdf2md import Options, convert, digital  # noqa: E402
import samples  # noqa: E402


def span_json(s):
    return {
        "text": s.text, "bbox": list(s.bbox), "size": s.size, "bold": s.bold, "italic": s.italic,
        "mono": s.mono, "sup": s.sup, "strike": s.strike, "link": s.link,
    }


def export(pdf: Path, out: Path) -> None:
    doc = fitz.open(pdf)
    pages = []
    for page in doc:
        data = digital.extract_page(page)
        tables = [{"bbox": list(b), "markdown": md} for b, md in digital._find_tables(page)]
        pages.append({
            "number": data.number, "width": data.width, "height": data.height,
            "blocks": [[{"bbox": list(l.bbox), "spans": [span_json(s) for s in l.spans]} for l in block]
                       for block in data.lines],
            "tables": tables,
        })
    result = convert(pdf, None, Options(engine="none", cache=False, verbose=0))
    kinds = [p.kind for p in result.pages]
    if any(k != "digital" for k in kinds):
        raise SystemExit(f"{pdf}: parity fixtures need all-digital pages, got {kinds}")
    out.write_text(json.dumps({"source": pdf.name, "pages": pages, "markdown": result.markdown},
                              ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"{out.name}: {len(pages)} pages, {len(result.markdown)} chars of Markdown")


def sample_pdfs(work: Path):
    """Our own generated documents (safe to commit)."""
    import test_digital_layout as layout_tests  # noqa: E402

    yield samples.digital_pdf(work / "digital.pdf")
    yield layout_tests.two_column_page_with_footnote(work / "paper.pdf")

    doc = fitz.open()
    for i in range(1, 6):
        p = doc.new_page()
        samples.put(p, 72, 50, [(f"Slide {i}", "b")], size=20)
        samples.put(p, 72, 120, [(f"Content of slide number {i} goes here in body text.", "r")])
        samples.put(p, 72, 820, [(f"Company Confidential {i}", "r")], size=8)
    doc.save(work / "slides.pdf")
    yield work / "slides.pdf"

    doc = fitz.open()
    text = ("Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor "
            "incididunt ut labore et dolore magna aliqua. ") * 12
    for i in range(6):
        p = doc.new_page()
        p.insert_text((72, 60), f"Chapter {i + 1}", fontsize=18)
        p.insert_textbox(fitz.Rect(72, 90, 520, 780), text, fontsize=11)
        p.insert_text((290, 815), str(i + 1), fontsize=9)
    doc.save(work / "book.pdf")
    yield work / "book.pdf"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("--pdf", action="append", default=[], help="extra PDFs to export")
    ap.add_argument("--no-samples", action="store_true")
    a = ap.parse_args()
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        sys.path.insert(0, str(ROOT / "pdf2md" / "tests"))
        pdfs = [] if a.no_samples else list(sample_pdfs(Path(tmp)))
        pdfs += [Path(p) for p in a.pdf]
        for pdf in pdfs:
            export(pdf, out / (pdf.stem + ".json"))
            if not a.no_samples and pdf.parent == Path(tmp):
                # The on-device tests read the same PDFs with MuPDF on Android.
                (out / pdf.name).write_bytes(pdf.read_bytes())


if __name__ == "__main__":
    main()
