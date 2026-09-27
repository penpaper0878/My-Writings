import pytest

from pdf2md import Options, convert


@pytest.fixture(scope="module")
def result(digital_pdf, tmp_path_factory):
    out = tmp_path_factory.mktemp("out") / "digital.md"
    return convert(digital_pdf, out, Options(cache=False, verbose=0))


def test_headings_ranked_by_size(result):
    md = result.markdown
    assert md.startswith("# Quarterly Notes\n")
    assert "\n## 1. Introduction\n" in md
    assert "\n## 2. Two Columns\n" in md


def test_emphasis_links_and_dehyphenation(result):
    assert (
        "This report has **bold facts** and *gentle italics* for every example we tried. "
        "See the project site for more details and the [full data](https://example.org/data) set, "
        "which is well-known." in result.markdown
    )


def test_nested_bullets_and_numbered_list(result):
    md = result.markdown
    assert "- Revenue grew in every region\n- Costs stayed flat\n  - Except for shipping\n- Hiring resumed" in md
    assert "1. Download the data\n2. Run the script" in md


def test_code_block_keeps_indentation(result):
    assert "```\ndef total(rows):\n    return sum(r.amount for r in rows)\n```" in result.markdown


def test_ruled_table(result):
    assert "| Region | Q1 | Q2 |\n| --- | --- | --- |\n| North | 120 | 135 |\n| South | 98 | 101 |" in result.markdown


def test_two_columns_in_reading_order(result):
    md = result.markdown
    order = [
        md.index("Left column starts here"),
        md.index("A second left paragraph"),
        md.index("Right column text begins"),
        md.index("Right side second para"),
    ]
    assert order == sorted(order)


def test_running_headers_and_page_numbers_removed(result):
    assert "ACME Quarterly Report" not in result.markdown
    assert "Page 1 of 3" not in result.markdown


def test_images_and_vector_figures_saved(result):
    md = result.markdown
    assert "![Page 3, image 1](digital_assets/page003-img1.png)" in md
    assert "![Page 3, figure 1](digital_assets/page003-fig1.png)" in md
    assets = result.output.parent / "digital_assets"
    assert sorted(p.name for p in assets.iterdir()) == ["page003-fig1.png", "page003-img1.png"]
    assert result.images == 2
    # The picture comes before the chart, and the caption after it.
    assert md.index("image 1") < md.index("figure 1") < md.index("The chart above")


def test_every_page_from_text_layer(result):
    assert [p.method for p in result.pages] == ["text", "text", "text"]
    assert not result.incomplete


def test_keep_headers_option(digital_pdf):
    res = convert(digital_pdf, None, Options(cache=False, verbose=0, strip_headers=False))
    assert "ACME Quarterly Report" in res.markdown


def test_no_images_to_stdout(digital_pdf):
    res = convert(digital_pdf, None, Options(cache=False, verbose=0))
    assert "![" not in res.markdown and res.images == 0
