"""Export parity fixtures for the OCR-side logic (prompt, clean-up, OCR layout, strips).

    python android/tools/export_ocr_parity.py android/core/src/test/resources/parity-ocr
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pdf2md"))
sys.path.insert(0, str(ROOT / "pdf2md" / "tests"))

from PIL import Image, ImageDraw  # noqa: E402

import samples  # noqa: E402
from pdf2md.converter import PageResult, strip_repeated_edges  # noqa: E402
from pdf2md.ocr import cleanup  # noqa: E402
from pdf2md.ocr.tesseract import _parse_tsv, tsv_to_markdown  # noqa: E402
from pdf2md.ocr.vlm import build_prompt  # noqa: E402
from pdf2md.render import find_cuts, is_blank  # noqa: E402

MODEL_OUTPUTS = [
    "Here is the transcription of the page:\n\n```markdown\n# Title\n\nHello\n```",
    "```markdown\n# Title\n\nHello\n```\nLet me know if you need anything else.",
    "# Notes\n\n```python\nx = 1\n```\n\nDone",
    "[blank]",
    "<think>hmm, let me look</think>\nText here",
    "```\nopened but cut off",
    "Line one\r\nLine two\r\n\r\n\r\n\r\nLine three",
    "Sure! Below is the Markdown text of the image:\n- item one\n- item two\nI hope this helps.",
    "Intro line\n" + "the cat sat on the mat. " * 40,
    "Real text.\n" + "\n".join(["same line"] * 30),
    "| a | b |\n|---|---|\n" + "|  |  |\n" * 8,
    "Contents" + " ." * 60,
    "Normal page with nothing odd.\n\nSecond paragraph.",
]


def words_from_tsv(tsv):
    return [
        {k: r[k] for k in ("text", "left", "top", "width", "height", "conf", "block", "par", "line")}
        for r in _parse_tsv(tsv) if r["level"] == 5
    ]


def tesseract_tsv(img):
    if not shutil.which("tesseract"):
        return None
    buf = img.convert("L")
    import io
    b = io.BytesIO()
    buf.save(b, "PNG")
    out = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "3", "--dpi", "300", "tsv"],
                         input=b.getvalue(), capture_output=True, check=True)
    return out.stdout.decode("utf-8", "replace")


def main(outdir, extra_images=()):
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    fx = {}

    fx["prompts"] = [
        {"part": p, "parts": n, "lang": lang, "hint": hint, "prompt": build_prompt(p, n, lang, hint)}
        for (p, n, lang, hint) in [
            (1, 1, None, None), (1, 2, None, None), (2, 3, "guj+hin+eng", None),
            (1, 1, "hin", "class 10 chemistry notes"), (1, 1, "eng,osd,xyz", "  spaced hint "),
        ]
    ]

    fx["cleanup"] = [
        {
            "input": t,
            "clean": cleanup.clean_model_output(t),
            "degenerate": cleanup.looks_degenerate(cleanup.clean_model_output(t)),
            "collapsed": cleanup.collapse_repetition(cleanup.clean_model_output(t)),
        }
        for t in MODEL_OUTPUTS
    ]

    # OCR layout from real Tesseract runs on generated pages
    layouts = []
    from conftest import PRINTED_LINES
    pages = [
        samples.text_image(PRINTED_LINES, size=44),
        samples.text_image(["Big Title Here", "", "Body text line that is long enough to wrap around",
                            "and continue onto a second line of the paragraph.", "", "1. first step",
                            "2. second step", "", "Short line", "Another short one"], size=36),
    ]
    pages += [Image.open(p) for p in extra_images]
    for img in pages:
        tsv = tesseract_tsv(img)
        if tsv is None:
            continue
        md, conf = tsv_to_markdown(tsv)
        layouts.append({"words": words_from_tsv(tsv), "markdown": md, "confidence": conf})
    fx["layouts"] = layouts

    # edge stripping
    def page(n, md, method):
        return PageResult(n, "scanned", method, md)
    edge_cases = [
        [page(i, f"Journal of Things - Vol 3\n\nDate: {10 + i} March\n\nBody of page {i}.\n\n{i}", "tesseract") for i in range(1, 6)],
        [page(i, f"Header text\n\nWorking for problem {i}.\n\n{40 + i}", "vlm") for i in range(1, 6)],
        [page(i, "Same header\n\nText.", "vlm") for i in range(1, 4)],
        [page(i, f"# Logo junk {i % 2}\n\nSprouts - http://x.org/9\n\nBody {i}\n\nFooter line", "tesseract") for i in range(1, 7)],
    ]
    fx["edges"] = []
    for case in edge_cases:
        before = [{"markdown": p.markdown, "printed": p.method == "tesseract"} for p in case]
        strip_repeated_edges(case)
        fx["edges"].append({"pages": before, "after": [p.markdown for p in case]})

    # strip cuts and blank detection on grey images (saved as PNG next to the JSON)
    imgs = []
    lined = samples.text_image([f"Line number {i} of the handwritten page" for i in range(20)], size=40, height=2000, spacing=2.2)
    ruled = lined.convert("RGB")
    d = ImageDraw.Draw(ruled)
    for y in range(100, ruled.height, 60):
        d.line((0, y, ruled.width, y), fill=(150, 170, 220), width=2)
    white = Image.new("L", (1654, 2339), 255)
    word = white.copy()
    from PIL import ImageFont
    ImageDraw.Draw(word).text((800, 1200), "hi", fill=170, font=ImageFont.load_default(size=36))
    for name, img in [("lined", lined), ("ruled", ruled), ("white", white), ("word", word)]:
        gray = img.convert("L")
        gray.save(out / f"{name}.png")
        imgs.append({
            "file": f"{name}.png",
            "cuts": {str(n): find_cuts(gray, n) for n in (2, 3, 4)},
            "blank": is_blank(gray),
        })
    fx["images"] = imgs

    (out / "ocr.json").write_text(json.dumps(fx, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"prompts {len(fx['prompts'])}, cleanup {len(fx['cleanup'])}, layouts {len(layouts)}, "
          f"edges {len(fx['edges'])}, images {len(imgs)}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
