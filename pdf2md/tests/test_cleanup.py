from pdf2md.ocr.cleanup import clean_model_output


def test_doubled_list_markers_are_merged():
    # What a small model wrote for handwritten "- " bullets.
    out = clean_model_output("Notes\n\n- - Light reactions make ATP\n  * • nested dot\n- – en dash")
    assert out == "Notes\n\n- Light reactions make ATP\n  * nested dot\n- en dash\n"


def test_rules_checkboxes_and_signs_are_kept():
    text = "- - -\n* * *\n- [ ] - todo\n- + 5 volts\n- -5 degrees\n-- not a list"
    assert clean_model_output(text) == text + "\n"
