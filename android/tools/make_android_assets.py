"""PDFs for the on-device classification and OCR tests.

    python android/tools/make_android_assets.py android/app/src/androidTest/assets
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pdf2md" / "tests"))

import pymupdf as fitz  # noqa: E402

import samples  # noqa: E402
from conftest import PRINTED_LINES  # noqa: E402


def main(outdir):
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    samples.image_pdf(out / "scanned.pdf", [samples.text_image(PRINTED_LINES, size=44)])
    samples.image_pdf(out / "hidden-ocr-layer.pdf", [samples.text_image(["Some handwriting"], size=40)],
                      invisible_text="garbled scanner ocr text layer here")
    samples.ink_pdf(out / "ink.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Typed lecture handout with a student's pen annotation.", fontsize=11)
    page.add_ink_annot([[(100, 200), (120, 210), (140, 205), (160, 220)]])
    doc.save(out / "annotated.pdf")
    doc = fitz.open()
    doc.new_page()
    doc.save(out / "blank.pdf")
    print("wrote", sorted(p.name for p in out.iterdir()))


if __name__ == "__main__":
    main(sys.argv[1])
