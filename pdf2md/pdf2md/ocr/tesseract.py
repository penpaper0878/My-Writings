"""Tesseract OCR for printed scans, turned into structured Markdown.

Tesseract is fast, small and fully offline, and very good on clean printed
pages. It is not built for handwriting: for handwritten notes use a vision
model (see vlm.py). Pages it reads with low confidence are reported.
"""

from __future__ import annotations

import csv
import io
import os
import shutil
import statistics
import subprocess
import sys
from typing import Dict, List, Optional

from PIL import Image

from .. import mdtext
from ..render import png_bytes
from .base import Engine, EngineError, EngineUnavailable, OcrResult

WINDOWS_PATHS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)
LOW_CONFIDENCE = 60


def find_tesseract(cmd: Optional[str] = None) -> Optional[str]:
    for candidate in (cmd, os.environ.get("TESSERACT_CMD"), "tesseract"):
        if candidate:
            found = shutil.which(candidate)
            if found:
                return found
    if sys.platform == "win32":
        for path in WINDOWS_PATHS:
            if os.path.exists(path):
                return path
    return None


def installed_languages(cmd: str) -> List[str]:
    out = subprocess.run([cmd, "--list-langs"], capture_output=True, text=True, timeout=30)
    text = out.stdout or out.stderr
    return [l.strip() for l in text.splitlines()[1:] if l.strip()]


class TesseractEngine(Engine):
    name = "tesseract"
    preferred_dpi = 300
    reads_handwriting = False

    def __init__(self, lang: Optional[str] = None, cmd: Optional[str] = None, psm: int = 3):
        self.lang = (lang or "eng").replace(",", "+")
        self.cmd_hint = cmd
        self.cmd: Optional[str] = None
        self.psm = psm
        self.version = ""
        self.default_workers = max(1, min(4, os.cpu_count() or 1))

    def describe(self) -> str:
        return f"Tesseract {self.version} ({self.lang})".replace("  ", " ")

    def signature(self) -> str:
        return f"tesseract|{self.version}|{self.lang}|{self.psm}|2"

    def check(self) -> None:
        self.cmd = find_tesseract(self.cmd_hint)
        if not self.cmd:
            raise EngineUnavailable(
                "Tesseract is not installed (Ubuntu/Debian: sudo apt install tesseract-ocr, "
                "macOS: brew install tesseract, Windows: https://github.com/UB-Mannheim/tesseract/wiki)"
            )
        try:
            v = subprocess.run([self.cmd, "--version"], capture_output=True, text=True, timeout=30)
            first = (v.stdout or v.stderr).splitlines()
            self.version = first[0].split()[-1] if first else ""
            have = set(installed_languages(self.cmd))
        except (OSError, subprocess.SubprocessError) as e:
            raise EngineUnavailable(f"could not run Tesseract at {self.cmd}: {e}") from e
        missing = [l for l in self.lang.split("+") if l and l not in have]
        if missing:
            pkgs = " ".join(f"tesseract-ocr-{m.replace('_', '-')}" for m in missing)
            raise EngineUnavailable(
                f"Tesseract has no data for {', '.join(missing)} (installed: {', '.join(sorted(have)) or 'none'}). "
                f"Ubuntu/Debian: sudo apt install {pkgs}"
            )

    def transcribe(self, image: Image.Image, page_number: int) -> OcrResult:
        if not self.cmd:
            self.check()
        dpi = int((image.info.get("dpi") or (300, 300))[0])
        env = dict(os.environ)
        env.setdefault("OMP_THREAD_LIMIT", "1")
        try:
            proc = subprocess.run(
                [self.cmd, "stdin", "stdout", "-l", self.lang, "--psm", str(self.psm), "--dpi", str(dpi), "tsv"],
                input=png_bytes(image.convert("L")),
                capture_output=True,
                timeout=600,
                env=env,
            )
        except (OSError, subprocess.SubprocessError) as e:
            raise EngineError(f"Tesseract failed: {e}") from e
        if proc.returncode != 0:
            raise EngineError(f"Tesseract failed: {proc.stderr.decode('utf-8', 'replace').strip()[:300]}")
        md, confidence = tsv_to_markdown(proc.stdout.decode("utf-8", "replace"))
        warnings = []
        if confidence is not None and confidence < LOW_CONFIDENCE and md.strip():
            warnings.append(
                f"page {page_number}: Tesseract was unsure of this page (confidence {confidence:.0f}%). "
                "If it is handwritten, use a vision model: --engine vlm"
            )
        return OcrResult(md, warnings)


def _parse_tsv(tsv: str) -> List[Dict]:
    rows = []
    reader = csv.reader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
    header = next(reader, None)
    if not header:
        return rows
    for r in reader:
        if len(r) < 12:
            continue
        rec = dict(zip(header, r))
        try:
            rows.append(
                {
                    "level": int(rec["level"]),
                    "block": int(rec["block_num"]),
                    "par": int(rec["par_num"]),
                    "line": int(rec["line_num"]),
                    "left": int(rec["left"]),
                    "top": int(rec["top"]),
                    "width": int(rec["width"]),
                    "height": int(rec["height"]),
                    "conf": float(rec["conf"]),
                    "text": rec.get("text", "") or "",
                }
            )
        except (KeyError, ValueError):
            continue
    return rows


def _lines(words: List[Dict]) -> List[dict]:
    """Group words into lines, in Tesseract's reading order."""
    lines: Dict[tuple, dict] = {}
    for w in words:
        key = (w["block"], w["par"], w["line"])
        ln = lines.setdefault(
            key, {"block": w["block"], "words": [], "x0": w["left"], "x1": 0, "top": w["top"], "bottom": 0}
        )
        ln["words"].append(w)
        ln["x0"] = min(ln["x0"], w["left"])
        ln["x1"] = max(ln["x1"], w["left"] + w["width"])
        ln["top"] = min(ln["top"], w["top"])
        ln["bottom"] = max(ln["bottom"], w["top"] + w["height"])
    out = [lines[k] for k in sorted(lines)]
    for ln in out:
        ln["text"] = mdtext.normalize_text(" ".join(w["text"] for w in ln["words"]))
        # Median word height is steadier than the line box, which descenders stretch.
        ln["size"] = statistics.median(w["height"] for w in ln["words"])
    return out


def _paragraphs(lines: List[dict]) -> List[List[dict]]:
    """Split lines into paragraphs by the page's own line spacing.

    Tesseract's paragraph numbers are unreliable (titles glued to the text
    below, paragraphs cut in two), so the vertical gaps decide instead.
    """
    height = statistics.median(l["bottom"] - l["top"] for l in lines)
    gaps = [
        b["top"] - a["bottom"]
        for a, b in zip(lines, lines[1:])
        if b["block"] == a["block"] and b["top"] > a["top"]
    ]
    typical = statistics.median(gaps) if gaps else 0.3 * height
    limit = max(typical * 1.8, typical + 0.5 * height)
    paras: List[List[dict]] = []
    for ln in lines:
        if paras:
            prev = paras[-1][-1]
            gap = ln["top"] - prev["bottom"]
            size_jump = abs(ln["size"] - prev["size"]) > 0.3 * max(ln["size"], prev["size"])
            if gap <= limit and ln["top"] > prev["top"] - 0.5 * height and not size_jump:
                paras[-1].append(ln)
                continue
        paras.append([ln])
    return paras


def _join(lines: List[dict], vocab: set) -> str:
    """Join a paragraph's lines, keeping a break where a short line is followed by a new statement."""
    left = min(l["x0"] for l in lines)
    right = max(l["x1"] for l in lines)
    width = max(right - left, 1)
    out = lines[0]["text"].strip()
    for prev, cur in zip(lines, lines[1:]):
        t = cur["text"].strip()
        short = right - prev["x1"] > 0.15 * width
        starts_new = t[:1].isupper() or t[:1].isdigit()
        if short and starts_new and not prev["text"].rstrip().endswith(("-", ",", "\u00ad")):
            out += "\n" + t
        else:
            out = mdtext.join_two(out, t, vocab)
    return out


def _md_lines(text: str) -> str:
    return "\n".join(mdtext.escape_line_start(mdtext.escape_inline(l)) for l in text.split("\n"))


def tsv_to_markdown(tsv: str):
    """Build Markdown from Tesseract's TSV (block / paragraph / line / word boxes).

    Returns (markdown, mean word confidence or None).
    """
    rows = _parse_tsv(tsv)
    words = [r for r in rows if r["level"] == 5 and r["conf"] >= 0 and r["text"].strip()]
    if not words:
        return "", None
    # A "word" far taller and narrower than its letters allow is a misread
    # column of numbers, rotated margin text or a logo: noise, not content.
    typical = statistics.median(w["height"] for w in words)
    words = [
        w for w in words
        if not (w["height"] > 2.5 * typical and w["width"] / max(w["height"], 1) < 0.3 * len(w["text"].strip()))
    ]
    if not words:
        return "", None
    # Lines of one to three stray symbols ("|", "~ .") are specks and rules.
    lines = [l for l in _lines(words) if any(ch.isalnum() for ch in l["text"]) or len(l["text"].strip()) > 3]
    if not lines:
        return "", None
    body = statistics.median(l["size"] for l in lines)
    vocab = mdtext.build_vocab(l["text"] for l in lines)
    blocks: List[str] = []
    list_run: List[str] = []

    def flush():
        if list_run:
            blocks.append("\n".join(list_run))
            list_run.clear()

    for plines in _paragraphs(lines):
        size = statistics.median(l["size"] for l in plines)
        text_all = " ".join(l["text"] for l in plines)
        if len(plines) <= 2 and size >= 1.45 * body and len(text_all) <= 120:
            flush()
            level = 1 if size >= 2.2 * body else 2
            blocks.append("#" * level + " " + mdtext.escape_inline(mdtext.join_lines([l["text"] for l in plines], vocab)))
            continue
        # A paragraph can hold several list items.
        items: List[List[dict]] = []
        for l in plines:
            if mdtext.split_list_marker(l["text"]) or not items:
                items.append([l])
            else:
                items[-1].append(l)
        first_marker = mdtext.split_list_marker(items[0][0]["text"])
        if first_marker and (first_marker[0] != "labelled" or len(items) >= 2):
            for item in items:
                # Every item starts on a marker line: that is how they were split.
                m = mdtext.split_list_marker(item[0]["text"])
                rest = [m[2]] + [l["text"] for l in item[1:]]
                list_run.append(f"{m[1]} " + mdtext.escape_line_start(mdtext.escape_inline(mdtext.join_lines(rest, vocab))))
            continue
        flush()
        first = plines[0]
        width = max(l["x1"] for l in plines) - min(l["x0"] for l in plines)
        if (
            len(plines) >= 2
            and first["x1"] - first["x0"] < 0.6 * width
            and len(first["words"]) <= 8
            and not mdtext.ends_sentence(first["text"])
            and first["text"].rstrip()[-1:] not in ",-"
        ):
            # A short title line ("Introduction") sitting on its paragraph.
            blocks.append(_md_lines(first["text"].strip()))
            plines = plines[1:]
        blocks.append(_md_lines(_join(plines, vocab)))
    flush()
    confidence = statistics.mean(w["conf"] for w in words)
    md = "\n\n".join(b for b in blocks if b.strip())
    return (md + "\n" if md else ""), confidence
