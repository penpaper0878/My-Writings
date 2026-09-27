"""Reading order for page elements (text blocks, tables, images).

A recursive XY-cut: split the region into columns where a clean vertical
gutter runs all the way down; otherwise into horizontal bands at blank gaps.
Bands that share a gutter are put back together before recursing, so a
two-column page whose paragraphs happen to end at the same height is still
read column by column instead of zig-zagging across.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Sequence, TypeVar

T = TypeVar("T")

MIN_GUTTER = 6.0  # points
MIN_COLUMN_SHARE = 0.12  # a column must be at least this share of the region width


def _bbox(items, key):
    boxes = [key(i) for i in items]
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _split(items: Sequence[T], key, axis: int, min_gap: float) -> List[List[T]]:
    """Group items whose extents along the axis overlap; groups are separated by gaps."""
    lo, hi = (0, 2) if axis == 0 else (1, 3)
    ordered = sorted(items, key=lambda i: key(i)[lo])
    groups: List[List[T]] = []
    end = None
    for it in ordered:
        b = key(it)
        if end is None or b[lo] > end + min_gap:
            groups.append([it])
            end = b[hi]
        else:
            groups[-1].append(it)
            end = max(end, b[hi])
    return groups


def _columns(items, key, nlines):
    """Split into columns if there is a real gutter, else return None."""
    if len(items) < 2:
        return None
    groups = _split(items, key, axis=0, min_gap=MIN_GUTTER)
    if len(groups) < 2:
        return None
    x0, _, x1, _ = _bbox(items, key)
    width = max(x1 - x0, 1.0)
    for g in groups:
        gx0, _, gx1, _ = _bbox(g, key)
        if (gx1 - gx0) / width < MIN_COLUMN_SHARE:
            return None
    for a, b in zip(groups, groups[1:]):
        if _row_aligned(a, b, key, nlines):
            return None
    return groups


def _row_aligned(a, b, key, nlines) -> bool:
    """True if two side-by-side groups line up item for item, like a form or grid.

    Those read across (label, value, label, value) rather than down. Only
    one-line items count: paragraphs that happen to start level in two
    columns are still columns.
    """
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    if len(small) < 2:
        return False
    hits = 0
    for it in small:
        if nlines(it) > 1:
            continue
        bi = key(it)
        hi = bi[3] - bi[1]
        for other in big:
            if nlines(other) > 2:
                continue
            bo = key(other)
            ho = bo[3] - bo[1]
            if abs(bi[1] - bo[1]) <= max(2.0, 0.3 * min(hi, ho)):
                hits += 1
                break
    return hits / len(small) >= 0.6


def _row_sort(items: Sequence[T], key) -> List[T]:
    """Top-to-bottom, and left-to-right for items sitting on the same line."""
    ordered = sorted(items, key=lambda i: (key(i)[1], key(i)[0]))
    rows: List[List[T]] = []
    row_bottom = None
    row_top = None
    for it in ordered:
        b = key(it)
        h = max(b[3] - b[1], 1.0)
        if rows and row_bottom is not None:
            overlap = min(row_bottom, b[3]) - max(row_top, b[1])
            if overlap >= 0.5 * min(h, max(row_bottom - row_top, 1.0)):
                rows[-1].append(it)
                row_bottom = max(row_bottom, b[3])
                continue
        rows.append([it])
        row_top, row_bottom = b[1], b[3]
    out: List[T] = []
    for row in rows:
        out.extend(sorted(row, key=lambda i: key(i)[0]))
    return out


def reading_order(
    items: Sequence[T],
    key: Callable[[T], tuple],
    nlines: Optional[Callable[[T], int]] = None,
) -> List[T]:
    """Order items for reading. key gives an item's (x0, y0, x1, y1);
    nlines, if given, its number of text lines (used to tell forms from columns)."""
    nlines = nlines or (lambda _: 1)
    items = list(items)
    if len(items) <= 1:
        return items
    cols = _columns(items, key, nlines)
    if cols:
        out: List[T] = []
        for c in cols:
            out.extend(reading_order(c, key, nlines))
        return out
    bands = _split(items, key, axis=1, min_gap=0.5)
    if len(bands) > 1:
        merged = [bands[0]]
        for band in bands[1:]:
            if _columns(merged[-1] + band, key, nlines):
                merged[-1] = merged[-1] + band
            else:
                merged.append(band)
        if len(merged) > 1:
            out = []
            for band in merged:
                out.extend(reading_order(band, key, nlines))
            return out
        cols = _columns(merged[0], key, nlines)
        if cols:
            out = []
            for c in cols:
                out.extend(reading_order(c, key, nlines))
            return out
    return _row_sort(items, key)
