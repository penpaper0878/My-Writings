"""Build small PDFs with known content for the tests."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    import pymupdf as fitz
except ImportError:
    import fitz

BODY = 11
FONTS = {"r": "helv", "b": "hebo", "i": "heit", "m": "cour"}


def put(page, x, y, parts, size=BODY):
    """Write styled runs one after another on a baseline: parts = [(text, "r"|"b"|"i"|"m")]."""
    for text, style in parts:
        font = FONTS[style]
        page.insert_text((x, y), text, fontname=font, fontsize=size)
        x += fitz.get_text_length(text, fontname=font, fontsize=size)
    return x


def running(page, n, total):
    put(page, 72, 40, [("ACME Quarterly Report", "r")], size=9)
    put(page, 280, 810, [(f"Page {n} of {total}", "r")], size=9)


def digital_pdf(path: Path) -> Path:
    doc = fitz.open()
    total = 3

    # ---- page 1: headings, emphasis, links, lists, code, table
    p = doc.new_page()
    running(p, 1, total)
    put(p, 72, 90, [("Quarterly Notes", "b")], size=24)
    put(p, 72, 130, [("1. Introduction", "b")], size=16)
    y = 155
    put(p, 72, y, [("This report has ", "r"), ("bold facts", "b"), (" and ", "r"), ("gentle italics", "i"), (" for every exam-", "r")])
    put(p, 72, y + 14, [("ple we tried. See the project site for more details and", "r")])
    x = put(p, 72, y + 28, [("the ", "r")])
    x2 = put(p, x, y + 28, [("full data", "r")])
    p.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(x, y + 18, x2, y + 31), "uri": "https://example.org/data"})
    put(p, x2, y + 28, [(" set, which is well-known.", "r")])
    y = 215
    put(p, 72, y, [("Things we checked:", "r")])
    put(p, 80, y + 16, [("• Revenue grew in every region", "r")])
    put(p, 80, y + 32, [("• Costs stayed flat", "r")])
    put(p, 96, y + 48, [("• Except for shipping", "r")])
    put(p, 80, y + 64, [("• Hiring resumed", "r")])
    y = 305
    put(p, 72, y, [("Steps to reproduce:", "r")])
    put(p, 80, y + 16, [("1. Download the data", "r")])
    put(p, 80, y + 32, [("2. Run the script", "r")])
    y = 370
    put(p, 72, y, [("def total(rows):", "m")], size=10)
    put(p, 72, y + 13, [("    return sum(r.amount for r in rows)", "m")], size=10)
    # a ruled 3x3 table
    top, left, cw, rh = 420, 72, 140, 20
    for r in range(4):
        p.draw_line((left, top + r * rh), (left + 3 * cw, top + r * rh))
    for c in range(4):
        p.draw_line((left + c * cw, top), (left + c * cw, top + 3 * rh))
    cells = [["Region", "Q1", "Q2"], ["North", "120", "135"], ["South", "98", "101"]]
    for r, row in enumerate(cells):
        for c, val in enumerate(row):
            put(p, left + c * cw + 4, top + r * rh + 14, [(val, "r")], size=10)
    put(p, 72, 510, [("That is all for the first page.", "r")])

    # ---- page 2: full-width heading over two columns
    p = doc.new_page()
    running(p, 2, total)
    put(p, 72, 90, [("2. Two Columns", "b")], size=16)
    left_col = ["Left column starts here and", "keeps going for a few lines", "until it reaches the end.", "",
                "A second left paragraph", "follows after a gap."]
    right_col = ["Right column text begins", "only after the left one is", "finished being read.", "",
                 "Right side second para", "closes the page."]
    for i, line in enumerate(left_col):
        if line:
            put(p, 72, 120 + i * 14, [(line, "r")])
    for i, line in enumerate(right_col):
        if line:
            put(p, 320, 120 + i * 14, [(line, "r")])

    # ---- page 3: a picture and a vector chart
    p = doc.new_page()
    running(p, 3, total)
    put(p, 72, 90, [("3. Figures", "b")], size=16)
    img = Image.new("RGB", (300, 200), (30, 120, 200))
    ImageDraw.Draw(img).ellipse((50, 50, 250, 150), fill=(250, 200, 40))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    p.insert_image(fitz.Rect(72, 110, 372, 310), stream=buf.getvalue())
    # bar chart
    base = 560
    p.draw_line((72, base), (400, base))
    p.draw_line((72, base), (72, 360))
    for k, h in enumerate([80, 150, 120, 60, 170]):
        p.draw_rect(fitz.Rect(90 + k * 60, base - h, 130 + k * 60, base), color=(0, 0, 0), fill=(0.2, 0.5, 0.8))
    put(p, 72, 600, [("The chart above shows growth.", "r")])

    doc.save(path)
    return path


def text_image(lines, font_path=None, size=40, width=1654, height=2339, margin=120, spacing=1.6) -> Image.Image:
    """Render lines of text into a white page image (a fake scan)."""
    img = Image.new("L", (width, height), 255)
    d = ImageDraw.Draw(img)
    if font_path:
        font = ImageFont.truetype(str(font_path), size)
    else:
        font = ImageFont.load_default(size=size)
    y = margin
    for line in lines:
        if line:
            d.text((margin, y), line, fill=20, font=font)
        y += int(size * spacing)
    return img


def image_pdf(path: Path, images, invisible_text: str = "") -> Path:
    """A PDF whose pages are just images, as a scanner makes."""
    doc = fitz.open()
    for img in images:
        p = doc.new_page(width=595, height=842)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=90)
        p.insert_image(p.rect, stream=buf.getvalue())
        if invisible_text:
            p.insert_text((72, 100), invisible_text, fontsize=11, render_mode=3)
    doc.save(path)
    return path


def ink_pdf(path: Path, strokes: int = 40) -> Path:
    """Handwriting as vector strokes, the way tablet note apps export it."""
    doc = fitz.open()
    p = doc.new_page()
    rng = np.random.default_rng(1)
    for s in range(strokes):
        x = 72 + (s % 7) * 70
        y = 100 + (s // 7) * 45
        shape = p.new_shape()
        pts = [fitz.Point(x + k * 4, y + float(rng.normal(0, 5))) for k in range(16)]
        for k in range(0, len(pts) - 3, 3):
            shape.draw_bezier(pts[k], pts[k + 1], pts[k + 2], pts[k + 3])
        shape.finish(color=(0, 0, 0.6), width=1.5, closePath=False)
        shape.commit()
    doc.save(path)
    return path
