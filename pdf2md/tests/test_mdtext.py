from pdf2md import mdtext


def test_normalize_expands_ligatures_but_keeps_symbols():
    assert mdtext.normalize_text("ﬁne oﬃce x² ½") == "fine office x² ½"
    assert mdtext.normalize_text("a\u00a0b\u200bc") == "a bc"


def test_escape_inline_keeps_words_readable():
    assert mdtext.escape_inline("2 * 3 = 6") == "2 \\* 3 = 6"
    assert mdtext.escape_inline("snake_case stays") == "snake_case stays"
    assert mdtext.escape_inline("_lead and trail_") == "\\_lead and trail\\_"
    assert mdtext.escape_inline("a <div> tag") == "a \\<div> tag"
    assert mdtext.escape_inline("x < y") == "x < y"
    assert mdtext.escape_inline("see [1](x)") == "see \\[1](x)"


def test_escape_line_start():
    assert mdtext.escape_line_start("# not a heading") == "\\# not a heading"
    assert mdtext.escape_line_start("2019. A year") == "2019\\. A year"
    assert mdtext.escape_line_start("- dash") == "\\- dash"
    assert mdtext.escape_line_start("plain") == "plain"
    assert mdtext.escape_line_start("---") == "\\---"


def test_code_span_picks_a_safe_fence():
    assert mdtext.code_span("a`b") == "``a`b``"
    assert mdtext.code_span("plain") == "`plain`"


def test_list_markers():
    assert mdtext.split_list_marker("• item") == ("glyph", "-", "item")
    assert mdtext.split_list_marker("\uf0b7 symbol-font bullet") == ("glyph", "-", "symbol-font bullet")
    assert mdtext.split_list_marker("- dash item") == ("dash", "-", "dash item")
    assert mdtext.split_list_marker("-5 degrees") is None
    assert mdtext.split_list_marker("12. twelfth") == ("ordered", "12.", "twelfth")
    assert mdtext.split_list_marker("3) third") == ("ordered", "3)", "third")
    assert mdtext.split_list_marker("(b) second") == ("labelled", "- (b)", "second")
    assert mdtext.split_list_marker("☐ todo") == ("glyph", "- [ ]", "todo")
    assert mdtext.split_list_marker("☑ done") == ("glyph", "- [x]", "done")
    assert mdtext.split_list_marker("2019. The year") is None
    assert mdtext.split_list_marker("e.g. this") is None


def test_dehyphenation_uses_document_vocabulary():
    vocab = mdtext.build_vocab(["a well-known fact", "the example shows"])
    assert mdtext.join_two("for exam-", "ple", vocab) == "for example"
    assert mdtext.join_two("it is well-", "known", vocab) == "it is well-known"
    assert mdtext.join_two("COVID-", "19 cases", vocab) == "COVID-19 cases"
    assert mdtext.join_two("soft\u00ad", "ware", vocab) == "software"
    assert mdtext.join_two("one", "two", vocab) == "one two"
    assert mdtext.join_two("wait—", "what", vocab) == "wait—what"


def test_split_blocks_keeps_fenced_code_whole():
    md = "para one\n\n```\ncode\n\nmore code\n```\n\npara two"
    assert mdtext.split_blocks(md) == ["para one", "```\ncode\n\nmore code\n```", "para two"]


def test_join_chunks_rejoins_paragraph_cut_by_page_break():
    out = mdtext.join_chunks(["# T\n\nThe sentence goes on", "and ends here.\n\nNext para."])
    assert out == "# T\n\nThe sentence goes on and ends here.\n\nNext para.\n"


def test_join_chunks_leaves_finished_paragraphs_alone():
    out = mdtext.join_chunks(["It ended.", "New start."])
    assert out == "It ended.\n\nNew start.\n"


def test_join_chunks_undoes_hyphen_across_pages():
    out = mdtext.join_chunks(["the exam-", "ple is here."])
    assert out == "the example is here.\n"


def test_join_chunks_merges_tables():
    a = "| A | B |\n| --- | --- |\n| 1 | 2 |"
    b_repeat = "| A | B |\n| --- | --- |\n| 3 | 4 |"
    b_cont = "| 3 | 4 |\n| --- | --- |\n| 5 | 6 |"
    assert mdtext.join_chunks([a, b_repeat]) == "| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |\n"
    # Across pages a different header means a different table...
    assert mdtext.join_chunks([a, b_cont]).count("| --- |") == 2
    # ...but strips of one page are one table.
    merged = mdtext.join_chunks([a, b_cont], merge_tables="any")
    assert merged == "| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |\n| 5 | 6 |\n"


def test_tex_accents_are_rebuilt():
    assert mdtext.normalize_text("na¨ıve Schr¨odinger Fran¸cois") == "naïve Schrödinger François"
    # Only when a real accented letter results: this is an apostrophe.
    assert mdtext.normalize_text("don´t") == "don´t"


def test_paragraph_rejoined_across_floating_figure():
    blocks = [
        "The method, described in Section 4, covers",
        "![Page 2, figure 1](a.png)",
        "**Figure 1.** The pipeline.",
        "```\ncode\n```",
        "nested loops with separate trees.",
    ]
    assert mdtext.merge_continuations(blocks) == [
        "The method, described in Section 4, covers nested loops with separate trees.",
        "![Page 2, figure 1](a.png)",
        "**Figure 1.** The pipeline.",
        "```\ncode\n```",
    ]


def test_captions_are_never_merged():
    blocks = ["Figure 2: Growth by region", "shows the trend."]
    assert mdtext.merge_continuations(blocks) == blocks
    assert mdtext.is_caption("**Table 3.** Results") and not mdtext.is_caption("Table of contents")
