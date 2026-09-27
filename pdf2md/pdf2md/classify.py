"""Decide, page by page, whether the text layer can be trusted or the page must be read by OCR.

Kinds of page:

digital  real, visible text: extract it exactly.
scanned  no usable text layer (a scan, a photo, an unreadable font encoding,
         or handwriting stored as vector ink): transcribe the rendered image.
mixed    typed text *plus* handwriting (ink strokes or ink annotations):
         a vision model reads both; without one, fall back to the text layer.
empty    nothing on the page.

Scanner apps (Adobe Scan, CamScanner, ...) often lay an invisible OCR text
layer over the image. That layer is ignored: it is usually poor on handwriting
and the page is re-read instead.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

try:
    import pymupdf as fitz
except ImportError:  # older PyMuPDF
    import fitz

from .mdtext import is_private_use

MIN_TEXT_CHARS = 20
MAX_GARBAGE_RATIO = 0.3
MIN_INK_STROKES = 15
MIN_INK_COVERAGE = 0.03
GRID = 64


@dataclass
class PageProfile:
    number: int  # 1-based
    kind: str
    reason: str
    visible_chars: int = 0
    invisible_chars: int = 0
    garbage_ratio: float = 0.0
    image_coverage: float = 0.0
    largest_image: float = 0.0
    ink_strokes: int = 0
    ink_coverage: float = 0.0
    drawings: int = 0


def _mark(mask: np.ndarray, rect, page_rect) -> None:
    w, h = page_rect.width or 1, page_rect.height or 1
    x0 = int(max(0, (rect[0] - page_rect.x0) / w * GRID))
    y0 = int(max(0, (rect[1] - page_rect.y0) / h * GRID))
    x1 = int(min(GRID, np.ceil((rect[2] - page_rect.x0) / w * GRID)))
    y1 = int(min(GRID, np.ceil((rect[3] - page_rect.y0) / h * GRID)))
    if x1 > x0 and y1 > y0:
        mask[y0:y1, x0:x1] = True


def _is_garbage(ch: str) -> bool:
    o = ord(ch)
    return ch == "�" or o < 32 or is_private_use(ch) or 0xFFF0 <= o <= 0xFFFF


def _text_stats(page):
    visible = invisible = garbage = 0
    try:
        trace = page.get_texttrace()
    except Exception:
        trace = None
    if trace is None:
        text = page.get_text("text")
        chars = [c for c in text if not c.isspace()]
        garbage = sum(_is_garbage(c) for c in chars)
        return len(chars), 0, garbage
    page_rect = page.rect
    for span in trace:
        hidden = span.get("type") == 3 or span.get("opacity", 1) == 0
        for ch in span.get("chars", ()):
            code = ch[0]
            if code < 0:
                continue
            c = chr(code)
            if c.isspace():
                continue
            bbox = ch[3] if len(ch) > 3 else None
            if bbox is not None and not fitz.Rect(bbox).intersects(page_rect):
                continue
            if hidden:
                invisible += 1
            else:
                visible += 1
                if _is_garbage(c):
                    garbage += 1
    return visible, invisible, garbage


def _drawings(page):
    try:
        return page.get_cdrawings()
    except Exception:
        try:
            return page.get_drawings()
        except Exception:
            return []


def _is_stroke(path) -> bool:
    """Heuristic for a pen stroke drawn on top of a typed page.

    Only open, stroked (not filled) chains of curves count. Filled paths with
    many curves are just as often text converted to outlines, and chains of
    straight segments are usually chart lines, so neither is taken as
    handwriting on a page that has real text. (A page with no text layer is
    read by OCR whatever its drawings are.)
    """
    items = path.get("items") or ()
    kind = path.get("type") or ""
    if "f" in kind or len(items) < 5 or path.get("closePath"):
        return False
    rect = fitz.Rect(path.get("rect") or (0, 0, 0, 0))
    if rect.width > 0 and rect.height > 0 and min(rect.width, rect.height) > 300:
        return False  # page-sized shapes are backgrounds or frames
    curves = sum(1 for it in items if it[0] == "c")
    return curves >= 0.6 * len(items)


def profile_page(page) -> PageProfile:
    number = page.number + 1
    page_rect = page.rect
    visible, invisible, garbage = _text_stats(page)
    garbage_ratio = garbage / visible if visible else 0.0

    img_mask = np.zeros((GRID, GRID), dtype=bool)
    largest = 0.0
    page_area = page_rect.width * page_rect.height or 1.0
    try:
        infos = page.get_image_info()
    except Exception:
        infos = []
    for info in infos:
        r = fitz.Rect(info["bbox"]) & page_rect
        if r.is_empty:
            continue
        _mark(img_mask, r, page_rect)
        largest = max(largest, r.width * r.height / page_area)
    image_coverage = float(img_mask.mean())

    drawings = _drawings(page)
    ink_mask = np.zeros((GRID, GRID), dtype=bool)
    strokes = 0
    for path in drawings:
        if _is_stroke(path):
            strokes += 1
            _mark(ink_mask, path["rect"], page_rect)
    ink_annots = 0
    try:
        for annot in page.annots(types=[fitz.PDF_ANNOT_INK]) or ():
            ink_annots += 1
            strokes += max(1, len(annot.vertices or ()))
            _mark(ink_mask, annot.rect, page_rect)
    except Exception:
        pass
    ink_coverage = float(ink_mask.mean())

    prof = PageProfile(
        number=number,
        kind="digital",
        reason="",
        visible_chars=visible,
        invisible_chars=invisible,
        garbage_ratio=garbage_ratio,
        image_coverage=image_coverage,
        largest_image=largest,
        ink_strokes=strokes,
        ink_coverage=ink_coverage,
        drawings=len(drawings),
    )
    has_ink = ink_annots > 0 or (strokes >= MIN_INK_STROKES and ink_coverage >= MIN_INK_COVERAGE)
    readable = visible - garbage

    if visible >= MIN_TEXT_CHARS and garbage_ratio >= MAX_GARBAGE_RATIO:
        prof.kind, prof.reason = "scanned", "text layer uses an unreadable font encoding"
    elif readable < MIN_TEXT_CHARS:
        if invisible >= MIN_TEXT_CHARS and image_coverage > 0.05:
            prof.kind, prof.reason = "scanned", "scanned image with a hidden OCR layer"
        elif image_coverage > 0.02:
            prof.kind, prof.reason = "scanned", "page is an image"
        elif has_ink or strokes >= 3:
            prof.kind, prof.reason = "scanned", "handwriting stored as vector ink"
        elif len(drawings) >= 20:
            prof.kind, prof.reason = "scanned", "text drawn as outlines"
        elif visible:
            prof.kind, prof.reason = "digital", "short text"
        else:
            prof.kind, prof.reason = "empty", "blank page"
    elif has_ink:
        prof.kind, prof.reason = "mixed", "typed text with handwriting"
    else:
        prof.kind, prof.reason = "digital", "text layer"
    return prof
