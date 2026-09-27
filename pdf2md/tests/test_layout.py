from pdf2md.layout import reading_order


def order(items, lines=None):
    boxes = {name: box for name, box in items}
    counts = lines or {}
    result = reading_order(
        list(boxes), key=lambda n: boxes[n], nlines=lambda n: counts.get(n, 3)
    )
    return result


def test_single_column_top_to_bottom():
    items = [("b", (72, 200, 500, 240)), ("a", (72, 100, 500, 180)), ("c", (72, 260, 500, 300))]
    assert order(items) == ["a", "b", "c"]


def test_two_columns_read_down_then_across():
    items = [
        ("title", (72, 60, 520, 80)),
        ("L1", (72, 100, 280, 160)),
        ("R1", (310, 100, 520, 160)),
        ("L2", (72, 180, 280, 260)),
        ("R2", (310, 180, 520, 260)),
    ]
    assert order(items) == ["title", "L1", "L2", "R1", "R2"]


def test_columns_between_full_width_sections():
    items = [
        ("top", (72, 40, 520, 60)),
        ("L1", (72, 80, 280, 140)),
        ("R1", (310, 80, 520, 140)),
        ("L2", (72, 150, 280, 200)),
        ("R2", (310, 150, 520, 200)),
        ("wide", (72, 220, 520, 260)),
        ("L3", (72, 280, 280, 320)),
        ("R3", (310, 280, 520, 320)),
    ]
    assert order(items) == ["top", "L1", "L2", "R1", "R2", "wide", "L3", "R3"]


def test_form_rows_read_across():
    items = [
        ("name", (72, 100, 180, 112)),
        ("john", (200, 100, 400, 112)),
        ("city", (72, 120, 180, 132)),
        ("pune", (200, 120, 400, 132)),
        ("date", (72, 140, 180, 152)),
        ("today", (200, 140, 400, 152)),
    ]
    lines = {n: 1 for n, _ in items}
    assert order(items, lines) == ["name", "john", "city", "pune", "date", "today"]


def test_narrow_side_labels_do_not_make_columns():
    items = [("n", (60, 100, 70, 112)), ("text", (80, 100, 520, 200))]
    assert order(items) == ["n", "text"]
