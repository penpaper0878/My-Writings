"""Markdown text helpers: escaping, list markers, line joining, block merging."""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Optional

LIGATURES = {
    "ﬀ": "ff",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "ﬅ": "st",
    "ﬆ": "st",
}
_LIGATURE_RE = re.compile("[" + "".join(LIGATURES) + "]")
# Spaces that should read as ordinary spaces in Markdown.
_SPACES_RE = re.compile("[\u00a0\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]")
# Zero-width characters that only get in the way once the text is extracted.
_ZERO_WIDTH_RE = re.compile("[\u200b\u2060\ufeff]")


# TeX-made PDFs often draw an accent as a separate spacing character before
# its letter ("na\u00a8\u0131ve" for "naïve"). Map each to its combining form.
_SPACING_ACCENTS = {
    "\u00a8": "\u0308",  # diaeresis
    "\u00b4": "\u0301",  # acute
    "\u02cb": "\u0300",  # grave
    "\u02c6": "\u0302",  # circumflex
    "\u02dc": "\u0303",  # tilde
    "\u00b8": "\u0327",  # cedilla
    "\u02c7": "\u030c",  # caron
    "\u02d8": "\u0306",  # breve
    "\u02da": "\u030a",  # ring
    "\u00af": "\u0304",  # macron
    "\u02d9": "\u0307",  # dot above
    "\u02dd": "\u030b",  # double acute
}
_ACCENT_RE = re.compile("([" + "".join(_SPACING_ACCENTS) + "]) ?([A-Za-z\u0131\u0237])")


def _combine_accent(m) -> str:
    base = {"\u0131": "i", "\u0237": "j"}.get(m.group(2), m.group(2))
    combined = unicodedata.normalize("NFC", base + _SPACING_ACCENTS[m.group(1)])
    # Only when it makes a real letter: "don´t" is an apostrophe, not a "ť".
    return combined if len(combined) == 1 else m.group(0)


def normalize_text(text: str) -> str:
    """Expand ligatures, rebuild split accents and tidy invisible characters.

    Deliberately not NFKC: that would also turn "x²" into "x2" and "½" into
    "1⁄2", which changes what the document says.
    """
    text = _LIGATURE_RE.sub(lambda m: LIGATURES[m.group()], text)
    text = _SPACES_RE.sub(" ", text)
    text = _ZERO_WIDTH_RE.sub("", text)
    if any(ch in _SPACING_ACCENTS for ch in text):
        text = _ACCENT_RE.sub(_combine_accent, text)
    return text


# ---------------------------------------------------------------- escaping

_INLINE_ESCAPE_RE = re.compile(r"([\\`*])")
# "_" only starts emphasis next to a non-word character, so snake_case and
# file_names stay readable.
_UNDERSCORE_RE = re.compile(r"(?<![0-9A-Za-z])_|_(?![0-9A-Za-z])")
_HTML_TAG_RE = re.compile(r"<(?=[A-Za-z/!?])")
_LINK_TEXT_RE = re.compile(r"\[(?=[^\]]*\]\()")


def escape_inline(text: str) -> str:
    """Escape characters that Markdown would otherwise treat as formatting."""
    text = _INLINE_ESCAPE_RE.sub(r"\\\1", text)
    text = _UNDERSCORE_RE.sub(r"\\_", text)
    text = _HTML_TAG_RE.sub(r"\\<", text)
    text = _LINK_TEXT_RE.sub(r"\\[", text)
    return text


_LINE_START_RES = (
    (re.compile(r"^(#{1,6})(?=\s|$)"), lambda m: "\\" + m.group(1)),
    (re.compile(r"^>"), lambda m: "\\>"),
    (re.compile(r"^([-+])(?=\s)"), lambda m: "\\" + m.group(1)),
    (re.compile(r"^(\d{1,9})([.)])(?=\s|$)"), lambda m: m.group(1) + "\\" + m.group(2)),
    (re.compile(r"^(=+|-+)\s*$"), lambda m: "\\" + m.group(0)),
    (re.compile(r"^(```|~~~)"), lambda m: "\\" + m.group(1)),
    (re.compile(r"^\|"), lambda m: "\\|"),
)


def escape_line_start(line: str) -> str:
    """Escape a line's first characters if they would start a Markdown block."""
    for pattern, repl in _LINE_START_RES:
        new = pattern.sub(repl, line, count=1)
        if new != line:
            return new
    return line


def escape_table_cell(text: str) -> str:
    text = " ".join(text.split())
    return escape_inline(text).replace("|", "\\|")


def code_span(text: str) -> str:
    """Wrap text in a backtick code span long enough not to clash with it."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def fence_for(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"^`{3,}", text, re.M)), default=0)
    return "`" * max(3, longest + 1)


# ------------------------------------------------------------ list markers

BULLET_CHARS = set("•◦▪▫‣⁃●○■□►▸▹▶➢➤➣✓✔✗✘❖◆◇◉⦿∙·*-–")
CHECK_OPEN = set("☐❑❒")
CHECK_DONE = set("☑☒✅")

_ORDERED_RE = re.compile(r"^(\d{1,3})([.)])\s+(?=\S)")
_LABEL_RE = re.compile(r"^(\((?:\d{1,3}|[a-z]|[ivxlc]{1,5})\)|(?:[a-z]|[ivxlc]{1,5})[.)])\s+(?=\S)")


def is_private_use(ch: str) -> bool:
    return "\ue000" <= ch <= "\uf8ff"


def is_bullet_glyph(text: str) -> bool:
    """True if text is only a bullet glyph (as symbol fonts often emit it)."""
    t = text.strip()
    return len(t) == 1 and (t in BULLET_CHARS or t in CHECK_OPEN or t in CHECK_DONE or is_private_use(t))


def split_list_marker(text: str):
    """Recognise a list marker at the start of a line.

    Returns (kind, markdown_marker, rest) or None. kind is one of:

    glyph     a bullet symbol or checkbox: unambiguous
    dash      "-", "–" or "*" followed by a space
    ordered   "1." or "1)"
    labelled  "a)", "(iv)", "(2)": kept verbatim after a "-" bullet, since
              Markdown has no lettered lists and the label must not be lost

    dash, ordered and labelled can also be ordinary text that wrapped onto a
    new line, so callers should check the context before trusting them.
    """
    s = text.lstrip()
    if not s:
        return None
    first = s[0]
    rest = s[1:]
    if first in CHECK_OPEN or first in CHECK_DONE:
        box = "[x]" if first in CHECK_DONE else "[ ]"
        return "glyph", f"- {box}", rest.lstrip()
    if first in BULLET_CHARS or is_private_use(first):
        if not rest.strip():
            return None
        if first in "-*–":
            # Only with a space after them: "-5" is a number, "*emphasis*" is not a list.
            if not rest[:1].isspace():
                return None
            return "dash", "-", rest.lstrip()
        return "glyph", "-", rest.lstrip()
    m = _ORDERED_RE.match(s)
    if m:
        return "ordered", f"{m.group(1)}{m.group(2)}", s[m.end():]
    m = _LABEL_RE.match(s)
    if m:
        return "labelled", f"- {m.group(1)}", s[m.end():]
    return None


# ------------------------------------------------------------ line joining

SENTENCE_END = tuple(".!?:;।॥。！？…")
_CLOSERS = "\"'”’»)]}*_`"


def ends_sentence(text: str) -> bool:
    t = text.rstrip().rstrip(_CLOSERS)
    return not t or t.endswith(SENTENCE_END)


_WORD_RE = re.compile(r"\w+(?:[-‐]\w+)*")


def build_vocab(texts: Iterable[str]) -> set:
    """Lower-cased words (hyphenated compounds kept whole) seen in the text."""
    vocab = set()
    for text in texts:
        for w in _WORD_RE.findall(text):
            w = w.lower().replace("‐", "-")
            vocab.add(w)
            if "-" in w:
                vocab.update(w.split("-"))
    return vocab


_TAIL_WORD_RE = re.compile(r"(\w+)[-‐]$")
_HEAD_WORD_RE = re.compile(r"(\w+)")


def joiner(prev: str, nxt: str, vocab: Optional[set] = None):
    """How to join two wrapped lines of one paragraph.

    Returns (drop, sep): drop this many characters from the end of the
    right-stripped first line, then insert sep. A line-end hyphen before a
    lower-case word is usually typesetting and is removed; the document's own
    vocabulary settles doubtful cases ("well-known" keeps its hyphen if the
    document spells it that way elsewhere).
    """
    prev = prev.rstrip()
    nxt = nxt.lstrip()
    if not prev or not nxt:
        return 0, ""
    if prev.endswith("\u00ad"):
        return 1, ""
    if prev[-1] in "-‐" and len(prev) >= 2 and prev[-2].isalpha():
        tail = _TAIL_WORD_RE.search(prev)
        head = _HEAD_WORD_RE.match(nxt)
        if tail and head and nxt[0].islower():
            a, b = tail.group(1), head.group(1)
            if vocab is not None:
                if (a + b).lower() in vocab:
                    return 1, ""
                if f"{a}-{b}".lower() in vocab:
                    return 0, ""
            return 1, ""
        return 0, ""
    if prev.endswith("—") or nxt.startswith("—"):
        return 0, ""
    if prev.endswith("/") and not prev.endswith(" /") and not nxt[0].isupper():
        return 0, ""
    return 0, " "


def join_two(prev: str, nxt: str, vocab: Optional[set] = None) -> str:
    """Join two wrapped lines of the same paragraph, undoing hyphenation."""
    prev = prev.rstrip()
    nxt = nxt.lstrip()
    if not prev:
        return nxt
    if not nxt:
        return prev
    drop, sep = joiner(prev, nxt, vocab)
    return prev[: len(prev) - drop] + sep + nxt


def join_lines(lines: Iterable[str], vocab: Optional[set] = None) -> str:
    out = ""
    for line in lines:
        out = join_two(out, line, vocab) if out else line.strip()
    return out


# ------------------------------------------------------- markdown blocks

_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")


def split_blocks(md: str) -> list:
    """Split Markdown into blank-line separated blocks, keeping fences whole."""
    blocks, cur, fence = [], [], None
    for line in md.splitlines():
        m = _FENCE_RE.match(line)
        if fence:
            cur.append(line)
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not line.strip()[len(m.group(1)):].strip():
                fence = None
            continue
        if m:
            fence = m.group(1)
            cur.append(line)
            continue
        if line.strip():
            cur.append(line.rstrip())
        elif cur:
            blocks.append("\n".join(cur))
            cur = []
    if cur:
        blocks.append("\n".join(cur))
    return blocks


_NON_PARAGRAPH_RE = re.compile(
    r"^\s*(#{1,6}\s|[-+*]\s|\d{1,9}[.)]\s|>|\||```|~~~|\$\$|!\[|<|\[(Figure|Diagram|Image)\b|\\\[)"
)


def is_paragraph(block: str) -> bool:
    return bool(block.strip()) and not _NON_PARAGRAPH_RE.match(block)


def is_table(block: str) -> bool:
    lines = block.splitlines()
    return len(lines) >= 2 and all(l.lstrip().startswith("|") for l in lines) and bool(
        re.match(r"^\s*\|?\s*:?-{3,}", lines[1])
    )


def _row_cells(row: str) -> list:
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|") and not row.endswith("\\|"):
        row = row[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", row)]


def _continues(prev: str, nxt: str) -> bool:
    last = prev.rstrip()
    first = nxt.lstrip()
    if not last or not first:
        return False
    if last[-1] in "-‐\u00ad" and first[0].islower():
        return True
    return not ends_sentence(last) and first[0].islower()


_CAPTION_RE = re.compile(
    r"^[*_]*(fig(ure)?|table|tab|listing|algorithm|chart|plate|exhibit|scheme)\.?\s*[\dIVX]", re.I
)


def is_caption(block: str) -> bool:
    return bool(_CAPTION_RE.match(block.lstrip()))


def is_float(block: str) -> bool:
    """Figures, code listings, tables and their captions: typesetting moves these around."""
    b = block.lstrip()
    return b.startswith(("![", "```", "~~~")) or is_table(block) or is_caption(block)


def _merge_paragraphs(prev: str, nxt: str, vocab: Optional[set]) -> str:
    head, _, last_line = prev.rpartition("\n")
    first_line, _, tail = nxt.partition("\n")
    merged = join_two(last_line, first_line, vocab)
    return (head + "\n" if head else "") + merged + ("\n" + tail if tail else "")


def merge_continuations(blocks: list, vocab: Optional[set] = None) -> list:
    """Re-join paragraphs that a column or page break cut in two.

    The second part must start in lower case and the first must not end a
    sentence. Up to four floating blocks (a figure, a code listing, a table,
    a caption) may sit between the parts, as they do when a figure is placed
    at the top of the next column; the joined paragraph then comes first.
    """
    out: list = []
    for b in blocks:
        if is_paragraph(b) and not is_caption(b) and b.lstrip()[:1].islower():
            k = len(out) - 1
            while k >= 0 and len(out) - 1 - k < 4 and is_float(out[k]):
                k -= 1
            if k >= 0 and is_paragraph(out[k]) and not is_caption(out[k]) and _continues(out[k], b):
                out[k] = _merge_paragraphs(out[k], b, vocab)
                continue
        out.append(b)
    return out


def join_chunks(chunks: Iterable[str], vocab: Optional[set] = None, merge_tables: str = "same-header") -> str:
    """Concatenate Markdown chunks (pages or page strips) into one document.

    Paragraphs cut by a break are re-joined (see merge_continuations). Tables
    split across chunks are merged when the continuation repeats the header
    (merge_tables="same-header") or, for strips of one page, whenever the
    column counts match (merge_tables="any").
    """
    out: list = []
    for chunk in chunks:
        blocks = split_blocks(chunk or "")
        if not blocks:
            continue
        if out and is_table(out[-1]) and is_table(blocks[0]):
            prev_rows, first_rows = out[-1].splitlines(), blocks[0].splitlines()
            prev_head, first_head = _row_cells(prev_rows[0]), _row_cells(first_rows[0])
            if len(prev_head) == len(first_head):
                if prev_head == first_head:
                    out[-1] = "\n".join(prev_rows + first_rows[2:])
                    blocks = blocks[1:]
                elif merge_tables == "any":
                    out[-1] = "\n".join(prev_rows + first_rows[:1] + first_rows[2:])
                    blocks = blocks[1:]
        out.extend(blocks)
    out = merge_continuations(out, vocab)
    return "\n\n".join(out).strip() + "\n" if out else ""
