"""Page images for OCR: rendering, light clean-up, and cutting tall pages into strips."""

from __future__ import annotations

import io
from typing import List

import numpy as np
from PIL import Image, ImageOps

try:
    import pymupdf as fitz
except ImportError:
    import fitz


def render_page(page, dpi: int, clip=None) -> Image.Image:
    """Render a page (or part of it) as it looks on screen, annotations and ink included."""
    pix = page.get_pixmap(dpi=dpi, clip=clip, alpha=False, annots=True)
    if pix.n != 3:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    img.info["dpi"] = (dpi, dpi)
    return img


def enhance(img: Image.Image) -> Image.Image:
    """Stretch contrast so faint pencil and grey photocopies read as ink on white."""
    out = ImageOps.autocontrast(img, cutoff=1)
    out.info.update(img.info)
    return out


def fit(img: Image.Image, max_side: int) -> Image.Image:
    w, h = img.size
    scale = max_side / max(w, h)
    if scale >= 1:
        return img
    out = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    out.info.update(img.info)
    return out


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=False)
    return buf.getvalue()


def _row_ink(img: Image.Image) -> np.ndarray:
    gray = np.asarray(img.convert("L"), dtype=np.int16)
    background = np.percentile(gray, 90)
    ink = gray < min(background - 60, 170)
    rows = ink.mean(axis=1)
    # Printed rules on lined paper cross the whole page: not text.
    rows[rows > 0.6] = 0.0
    return rows


def find_cuts(img: Image.Image, parts: int) -> List[int]:
    """Row positions that split the page into `parts` strips through blank space.

    Each cut is looked for near its ideal position, in the widest band of empty
    rows, so no line of writing is sliced in half.
    """
    if parts <= 1:
        return []
    rows = _row_ink(img)
    h = len(rows)
    blank = rows <= 0.002
    # Distance from each row to the nearest inked row: large in wide gaps.
    dist = np.zeros(h)
    run = 0
    for i in range(h):
        run = run + 1 if blank[i] else 0
        dist[i] = run
    run = 0
    for i in range(h - 1, -1, -1):
        run = run + 1 if blank[i] else 0
        dist[i] = min(dist[i], run)
    k = max(3, h // 200)
    smooth = np.convolve(rows, np.ones(k) / k, mode="same")
    cuts = []
    half_window = int(h / parts * 0.35)
    for n in range(1, parts):
        target = int(h * n / parts)
        lo, hi = max(1, target - half_window), min(h - 1, target + half_window)
        window = dist[lo:hi]
        if window.size and window.max() > 0:
            # Among rows in the widest gaps, take the one closest to the target.
            best = window.max()
            candidates = np.flatnonzero(window >= best * 0.8) + lo
            cut = int(candidates[np.argmin(np.abs(candidates - target))])
        else:
            cut = int(np.argmin(smooth[lo:hi]) + lo)
        if not cuts or cut - cuts[-1] > h * 0.1:
            cuts.append(cut)
    return cuts


def split_strips(img: Image.Image, parts: int, overlap: int = 0) -> List[Image.Image]:
    cuts = find_cuts(img, parts)
    edges = [0] + cuts + [img.height]
    strips = []
    for top, bottom in zip(edges, edges[1:]):
        box = (0, max(0, top - overlap), img.width, min(img.height, bottom + overlap))
        strip = img.crop(box)
        strip.info.update(img.info)
        strips.append(strip)
    return strips


def is_blank(img: Image.Image, threshold: float = 0.00002) -> bool:
    """True for a page with no marks at all, so the model is not asked to read nothing.

    Deliberately sensitive (faint pencil counts) and strict (one word is
    enough to be "not blank"): skipping a page with writing on it would lose
    content, while reading a blank one only costs time.
    """
    gray = np.asarray(img.convert("L"), dtype=np.int16)
    background = np.percentile(gray, 95)
    marks = gray < background - 40
    return float(marks.mean()) < threshold
