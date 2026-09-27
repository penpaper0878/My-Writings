import numpy as np
from PIL import Image, ImageDraw, ImageFont

import samples
from pdf2md.render import find_cuts, is_blank, split_strips


def lined_page(n_lines=20, height=2000):
    lines = [f"Line number {i} of the handwritten page" for i in range(n_lines)]
    return samples.text_image(lines, size=40, height=height, spacing=2.2)


def test_cuts_fall_in_blank_gaps():
    img = lined_page()
    gray = np.asarray(img.convert("L"))
    for parts in (2, 3, 4):
        cuts = find_cuts(img, parts)
        assert len(cuts) == parts - 1
        for c in cuts:
            assert gray[c].min() > 200, f"cut at row {c} goes through ink"


def test_strips_cover_the_page():
    img = lined_page()
    strips = split_strips(img, 3)
    assert len(strips) == 3
    assert sum(s.height for s in strips) == img.height
    assert all(s.width == img.width for s in strips)


def test_ruled_paper_lines_are_not_ink():
    img = lined_page().convert("RGB")
    d = ImageDraw.Draw(img)
    for y in range(100, img.height, 60):
        d.line((0, y, img.width, y), fill=(150, 170, 220), width=2)
    gray = np.asarray(img.convert("L"))
    for c in find_cuts(img, 2):
        # it may sit on a printed rule, never on writing
        row = gray[c]
        assert (row < 100).sum() == 0


def test_blank_detection():
    white = Image.new("RGB", (1654, 2339), "white")
    assert is_blank(white)
    # One short word in faint pencil is enough to make the page worth reading.
    one_word = white.copy()
    ImageDraw.Draw(one_word).text((800, 1200), "hi", fill=(170, 170, 170), font=ImageFont.load_default(size=36))
    assert not is_blank(one_word)
    noisy = Image.fromarray(
        np.clip(np.random.default_rng(0).normal(245, 3, (2339, 1654)), 0, 255).astype("uint8")
    ).convert("RGB")
    assert is_blank(noisy)
