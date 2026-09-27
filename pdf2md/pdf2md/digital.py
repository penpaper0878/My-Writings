"""Markdown from a page's own text layer: exact characters, reconstructed structure.

Structure comes from the typography the PDF already carries:

* headings    font sizes larger than the body text, ranked across the document
* emphasis    bold / italic / monospace / strikeout flags on each span
* lists       bullet glyphs and numbering, nested by indentation
* code        runs of monospaced lines, indentation preserved
* tables      ruled tables found by PyMuPDF, emitted as GFM tables
* links       URI link areas mapped onto the text they cover
* images      embedded pictures and vector figures, saved next to the output
* order       multi-column layouts read column by column (see layout.py)

Running headers, footers and page numbers are dropped when they repeat.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional
from urllib.parse import quote

try:
    import pymupdf as fitz
except ImportError:  # older PyMuPDF
    import fitz

from . import mdtext
from .layout import reading_order

# Keep MuPDF's own chatter out of our output (it would land in piped Markdown).
if hasattr(fitz, "no_recommend_layout"):
    fitz.no_recommend_layout()
try:
    fitz.TOOLS.mupdf_display_errors(False)
    fitz.TOOLS.mupdf_display_warnings(False)
except Exception:
    pass

TEXT_FLAGS = (
    fitz.TEXT_PRESERVE_WHITESPACE
    | fitz.TEXT_MEDIABOX_CLIP
    | getattr(fitz, "TEXT_COLLECT_STYLES", 0)
)
_FILLED = 16
_STROKED = 32
_STRIKEOUT = 1
_SYNTH_BOLD = 8

MONO_FONT_RE = re.compile(r"mono|courier|consol|menlo|code|inconsolata|fixed|typewriter|\bcmtt|sfmono", re.I)
BOLD_FONT_RE = re.compile(r"bold|black|heavy|semibold|demi", re.I)
ITALIC_FONT_RE = re.compile(r"italic|oblique|slanted", re.I)
MAX_HEADING_CHARS = 160
PAGE_NUMBER_RE = re.compile(r"^(page|p\.|seite|pg\.?)?\s*[#ivxlcdm]{1,6}\s*((of|/|von)\s*[#]+)?$", re.I)


# ----------------------------------------------------------------- model

@dataclass
class Span:
    text: str
    bbox: tuple
    size: float
    bold: bool = False
    italic: bool = False
    mono: bool = False
    sup: bool = False
    strike: bool = False
    link: Optional[str] = None

    def style(self, plain: bool = False):
        if plain:
            return (False, False, self.mono, False, False, self.link)
        return (self.bold, self.italic, self.mono, self.sup, self.strike, self.link)


@dataclass
class Line:
    spans: List[Span]
    bbox: tuple

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)

    @property
    def size(self) -> float:
        weights: Counter = Counter()
        for s in self.spans:
            weights[round(s.size * 2) / 2] += len(s.text.strip()) or 0.1
        return weights.most_common(1)[0][0] if weights else 0.0

    @property
    def mono(self) -> bool:
        spans = [s for s in self.spans if s.text.strip()]
        return bool(spans) and all(s.mono for s in spans)

    @property
    def x0(self) -> float:
        return self.bbox[0]


@dataclass
class Item:
    kind: str  # text | table | image
    bbox: tuple
    lines: List[Line] = field(default_factory=list)
    markdown: str = ""


@dataclass
class Frag:
    kind: str  # heading | para | list | code | table | image
    text: str
    x0: float = 0.0
    marker: str = ""
    bbox: Optional[tuple] = None
    level: int = 0
    alt: str = ""  # a "title?" candidate's text if it turns out not to be a heading


@dataclass
class PageData:
    number: int
    width: float
    height: float
    lines: List[List[Line]]  # blocks of lines, in content order


class DocStyle:
    """Typography measured over the whole document."""

    def __init__(self, pages: List[PageData]):
        sizes: Counter = Counter()
        texts = []
        for p in pages:
            for block in p.lines:
                for line in block:
                    for s in line.spans:
                        n = len(s.text.strip())
                        if n:
                            sizes[round(s.size * 2) / 2] += n
                    texts.append(line.text)
        self.body_size = sizes.most_common(1)[0][0] if sizes else 11.0
        body_chars = sizes[self.body_size] if sizes else 1
        threshold = max(self.body_size * 1.12, self.body_size + 1.0)
        candidates = sorted(
            (sz for sz, n in sizes.items() if sz >= threshold and n <= max(body_chars * 0.35, 400)),
            reverse=True,
        )
        # Sizes within half a point of each other are the same heading level.
        levels: List[float] = []
        for sz in candidates:
            if not levels or levels[-1] - sz >= 0.75:
                levels.append(sz)
        self.heading_sizes = levels[:6]
        self.vocab = mdtext.build_vocab(texts)

    def heading_level(self, size: float) -> int:
        for i, sz in enumerate(self.heading_sizes):
            if size >= sz - 0.5:
                return min(i + 1, 6)
        return 0


# ------------------------------------------------------------ extraction

# Only trust the filled/stroked bits where this MuPDF reports them.
_HAS_PAINT_FLAGS = getattr(getattr(fitz, "mupdf", None), "FZ_STEXT_FILLED", None) == _FILLED


def _span_is_hidden(span: dict) -> bool:
    """Invisible text: a scanner's OCR layer, or text rendered in mode 3."""
    font = span.get("font") or ""
    if font.split("+")[-1] == "GlyphLessFont":
        return True
    if span.get("alpha", 255) == 0:
        return True
    cf = span.get("char_flags")
    if _HAS_PAINT_FLAGS and isinstance(cf, int) and cf & (_FILLED | _STROKED) == 0:
        return True
    return False


def _uri_links(page) -> List[tuple]:
    out = []
    try:
        for ln in page.get_links():
            if ln.get("kind") == fitz.LINK_URI and ln.get("uri"):
                out.append((fitz.Rect(ln["from"]), ln["uri"]))
    except Exception:
        pass
    return out


def _link_for(bbox, links) -> Optional[str]:
    if not links:
        return None
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    for rect, uri in links:
        if rect.x0 - 1 <= cx <= rect.x1 + 1 and rect.y0 - 1 <= cy <= rect.y1 + 1:
            return uri
    return None


def extract_page(page) -> PageData:
    """Read the styled lines of one page, in the PDF's own block structure."""
    if page.rotation:
        try:
            page.remove_rotation()
        except Exception:
            pass
    links = _uri_links(page)
    # With links on the page, read per-character boxes so a link covering part
    # of a span marks exactly the words it covers.
    raw = page.get_text("rawdict" if links else "dict", flags=TEXT_FLAGS)
    blocks: List[List[Line]] = []
    for b in raw.get("blocks", ()):
        if b.get("type") != 0:
            continue
        lines: List[Line] = []
        for ln in b.get("lines", ()):
            d = ln.get("dir", (1, 0))
            if abs(d[1]) > 0.1 or d[0] <= 0:
                continue  # rotated margin text (arXiv stamps, watermarks)
            spans: List[Span] = []
            for s in ln.get("spans", ()):
                if _span_is_hidden(s):
                    continue
                font = s.get("font", "")
                flags = s.get("flags", 0)
                cf = s.get("char_flags", 0) or 0
                for text, bbox, link in _span_pieces(s, links):
                    text = mdtext.normalize_text(text)
                    if not text:
                        continue
                    spans.append(
                        Span(
                            text=text,
                            bbox=bbox,
                            size=float(s.get("size", 0.0)),
                            bold=bool(flags & 16) or bool(BOLD_FONT_RE.search(font)) or bool(cf & _SYNTH_BOLD),
                            italic=bool(flags & 2) or bool(ITALIC_FONT_RE.search(font)),
                            mono=bool(flags & 8) or bool(MONO_FONT_RE.search(font)),
                            sup=bool(flags & 1),
                            strike=bool(cf & _STRIKEOUT),
                            link=link,
                        )
                    )
            spans = _space_spans(spans)
            if spans and "".join(s.text for s in spans).strip():
                lines.append(Line(spans=spans, bbox=_union([s.bbox for s in spans])))
        lines = _merge_same_baseline(lines)
        if lines:
            blocks.append(lines)
    return PageData(page.number + 1, page.rect.width, page.rect.height, blocks)


def _span_pieces(span: dict, links):
    """Split a span into (text, bbox, uri) runs by the link areas its characters fall in."""
    chars = span.get("chars")
    if chars is None:
        return [(span.get("text", ""), tuple(span["bbox"]), None)]
    pieces = []
    for ch in chars:
        uri = _link_for(ch["bbox"], links) if ch["c"].strip() else (pieces[-1][2] if pieces else None)
        if pieces and pieces[-1][2] == uri:
            pieces[-1][0].append(ch["c"])
            pieces[-1][1].append(ch["bbox"])
        else:
            pieces.append([[ch["c"]], [ch["bbox"]], uri])
    out = []
    for texts, boxes, uri in pieces:
        text = "".join(texts)
        # A trailing space inside the link belongs outside it.
        if uri and text.endswith(" ") and len(text) > 1:
            out.append((text.rstrip(), _union(boxes), uri))
            out.append((" " * (len(text) - len(text.rstrip())), tuple(boxes[-1]), None))
        else:
            out.append((text, _union(boxes), uri))
    return out


def _union(boxes) -> tuple:
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _space_spans(spans: List[Span]) -> List[Span]:
    """Insert a space where two spans sit apart but neither carries one."""
    out: List[Span] = []
    for s in spans:
        if out:
            prev = out[-1]
            gap = s.bbox[0] - prev.bbox[2]
            if gap > 0.2 * max(s.size, 1) and not prev.text.endswith(" ") and not s.text.startswith(" "):
                prev.text += " "
        out.append(s)
    return out


def _merge_same_baseline(lines: List[Line]) -> List[Line]:
    """MuPDF sometimes splits one visual line (tabs, justification) into several."""
    out: List[Line] = []
    for ln in lines:
        if out:
            p = out[-1]
            h = min(p.bbox[3] - p.bbox[1], ln.bbox[3] - ln.bbox[1])
            overlap = min(p.bbox[3], ln.bbox[3]) - max(p.bbox[1], ln.bbox[1])
            if h > 0 and overlap >= 0.6 * h and ln.bbox[0] >= p.bbox[2] - 1:
                if not p.spans[-1].text.endswith(" "):
                    p.spans[-1].text += " "
                p.spans.extend(ln.spans)
                p.bbox = _union([p.bbox, ln.bbox])
                continue
        out.append(ln)
    return out


# ------------------------------------------------- running headers/footers

def _running_key(line: Line, body_size: float) -> str:
    """How a line is compared with other pages' margin lines.

    Small print (at most body size) is compared with its numbers blanked, so
    "Page 3 of 10" matches "Page 4 of 10". Anything larger (chapter or slide
    titles) must repeat word for word: "Chapter 1" and "Chapter 2" differ.
    """
    text = re.sub(r"\s+", " ", line.text.strip().lower())
    if line.size <= body_size * 1.05:
        return re.sub(r"\d+", "#", text)
    return "=" + text


def _in_margin(line: Line, page: PageData, margin: float) -> bool:
    return line.bbox[3] <= page.height * margin or line.bbox[1] >= page.height * (1 - margin)


def find_running_lines(pages: List[PageData], body_size: float, margin: float = 0.1) -> set:
    """Keys of lines that repeat in the top/bottom margin of many pages."""
    if len(pages) < 3:
        return set()
    counts: Counter = Counter()
    for p in pages:
        seen = set()
        for block in p.lines:
            for ln in block:
                if _in_margin(ln, p, margin):
                    seen.add(_running_key(ln, body_size))
        counts.update(seen)
    need = max(3, int(len(pages) * 0.4 + 0.999))
    return {t for t, n in counts.items() if n >= need and t.strip("=")}


def strip_running(page: PageData, running: set, body_size: float, margin: float = 0.1) -> None:
    """Remove running headers/footers and small page numbers from a page's margins."""
    kept_blocks = []
    for block in page.lines:
        kept = []
        for ln in block:
            if _in_margin(ln, page, margin):
                key = _running_key(ln, body_size)
                if key in running or (not key.startswith("=") and PAGE_NUMBER_RE.match(key)):
                    continue
            kept.append(ln)
        if kept:
            kept_blocks.append(kept)
    page.lines = kept_blocks


# ---------------------------------------------------------------- render

def render_spans(spans: List[Span], plain: bool = False) -> str:
    """Spans to inline Markdown, merging runs that share a style."""
    runs: List[list] = []  # [style, text]
    for s in spans:
        st = s.style(plain)
        # Keep symbol-only pieces apart from words, so "} " + "word" in one
        # italic font does not become "*} word*".
        if runs and runs[-1][0] == st and _has_alnum(runs[-1][1]) == _has_alnum(s.text):
            runs[-1][1] += s.text
        elif runs and not s.text.strip() and not runs[-1][0][2]:
            runs[-1][1] += s.text  # whitespace joins the previous run
        else:
            runs.append([st, s.text])
    out = []
    for (bold, italic, mono, sup, strike, link), text in runs:
        core = text.strip()
        if not core:
            out.append(text)
            continue
        lead = text[: len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()):]
        if mono:
            md = mdtext.code_span(core)
        else:
            md = mdtext.escape_inline(core)
            if sup:
                md = f"<sup>{md}</sup>"
            if not any(ch.isalnum() for ch in core):
                # "*{*" or "**,**": emphasis on bare symbols is typesetting noise.
                bold = italic = strike = False
            if strike:
                md = f"~~{md}~~"
            if bold and italic:
                md = f"***{md}***"
            elif bold:
                md = f"**{md}**"
            elif italic:
                md = f"*{md}*"
        if link:
            md = f"[{md}]({_link_target(link)})"
        out.append(lead + md + trail)
    return "".join(out)


def _introduces(title: Frag, nxt: Optional[Frag]) -> bool:
    """True if body text starts just below the title and lines up with it.

    That is what a sub-heading looks like; a bold label inside a diagram, or
    a bold line followed by another bold line, is not one.
    """
    if nxt is None or nxt.bbox is None or title.bbox is None or nxt.kind not in ("para", "list"):
        return False
    height = max(title.bbox[3] - title.bbox[1], 1.0)
    gap = nxt.bbox[1] - title.bbox[3]
    dx = nxt.bbox[0] - title.bbox[0]
    aligned = abs(dx) <= 6 or (nxt.kind == "list" and 0 <= dx <= 30)
    return -1 <= gap <= 2.5 * height and aligned


def _is_bold_title(line: Line) -> bool:
    """A short, fully bold line that reads like a title, not a bold lead-in or label."""
    spans = [s for s in line.spans if s.text.strip()]
    text = line.text.strip()
    return (
        bool(spans)
        and all(s.bold and not s.mono for s in spans)
        and 2 <= len(text) <= 80
        and (text[0].isupper() or text[0].isdigit())
        and text[-1] not in ".,;:!?"
        and _has_alnum(text)
    )


def _has_alnum(text: str) -> bool:
    return any(ch.isalnum() for ch in text)


def _link_target(uri: str) -> str:
    return uri.replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def _join_line_spans(lines: List[Line], vocab) -> List[Span]:
    """One span list for a wrapped paragraph, hyphenation undone at the joins."""
    out: List[Span] = []
    prev_text = ""
    for ln in lines:
        spans = [Span(**vars(s)) for s in ln.spans]
        # Leading spaces of a new line are layout, not content.
        while spans and not spans[0].text.strip():
            spans.pop(0)
        if not spans:
            continue
        spans[0].text = spans[0].text.lstrip()
        text = "".join(s.text for s in spans)
        if out:
            while out and not out[-1].text.strip():
                out.pop()
            out[-1].text = out[-1].text.rstrip()
            drop, sep = mdtext.joiner(prev_text, text, vocab)
            if drop:
                out[-1].text = out[-1].text[:-drop]
            if sep:
                out[-1].text += sep
        out.extend(spans)
        prev_text = text
    if out:
        out[-1].text = out[-1].text.rstrip()
    return out


def _drop_chars(spans: List[Span], n: int) -> List[Span]:
    out = [Span(**vars(s)) for s in spans]
    while n > 0 and out:
        s = out[0]
        if len(s.text) <= n:
            n -= len(s.text)
            out.pop(0)
        else:
            s.text = s.text[n:]
            n = 0
    if out:
        out[0].text = out[0].text.lstrip()
    return out


class PageRenderer:
    def __init__(self, style: DocStyle):
        self.style = style

    # ---- lines -> fragments
    def block_fragments(self, lines: List[Line]) -> List[Frag]:
        style = self.style
        frags: List[Frag] = []
        left = min(l.bbox[0] for l in lines)
        right = max(l.bbox[2] for l in lines)
        i = 0
        n = len(lines)
        in_list = False
        while i < n:
            ln = lines[i]
            text = ln.text.strip()
            level = style.heading_level(ln.size) if not ln.mono else 0
            if level and len(text) <= 200:
                group = [ln]
                j = i + 1
                while j < n and style.heading_level(lines[j].size) == level and not lines[j].mono:
                    gap = lines[j].bbox[1] - group[-1].bbox[3]
                    if gap > 0.8 * ln.size:
                        break
                    group.append(lines[j])
                    j += 1
                body = render_spans(_join_line_spans(group, style.vocab), plain=True).strip()
                if len(body) <= MAX_HEADING_CHARS and len(group) <= 3:
                    frags.append(
                        Frag("heading", body, bbox=_union([l.bbox for l in group]), level=level)
                    )
                else:
                    # Too long for a heading (an author list, a large-print intro): prose.
                    frags.append(Frag("para", self._paragraph(group, left, right)))
                i = j
                in_list = False
                continue
            if ln.mono:
                group = [ln]
                j = i + 1
                while j < n and lines[j].mono:
                    group.append(lines[j])
                    j += 1
                frags.append(Frag("code", self._code(group)))
                i = j
                in_list = False
                continue
            marker = mdtext.split_list_marker(text)
            prev_ok = i == 0 or in_list or mdtext.ends_sentence(lines[i - 1].text)
            if marker and (marker[0] == "glyph" or prev_ok):
                kind, md_marker, rest = marker
                drop = len(ln.text) - len(ln.text.lstrip()) + (len(text) - len(rest))
                item_lines = [Line(_drop_chars(ln.spans, drop), ln.bbox)]
                text_x0 = item_lines[0].spans[0].bbox[0] if item_lines[0].spans else ln.x0
                j = i + 1
                while j < n:
                    nxt = lines[j]
                    t = nxt.text.strip()
                    if mdtext.split_list_marker(t) or nxt.mono or style.heading_level(nxt.size) != level:
                        break
                    gap = nxt.bbox[1] - item_lines[-1].bbox[3]
                    if gap > 0.8 * max(nxt.size, 1):
                        break
                    indented = nxt.x0 >= text_x0 - 2
                    if not indented and mdtext.ends_sentence(item_lines[-1].text):
                        break
                    item_lines.append(nxt)
                    j += 1
                body = render_spans(_join_line_spans(item_lines, style.vocab)).strip()
                frags.append(
                    Frag(
                        "list", mdtext.escape_line_start(body), x0=ln.x0, marker=md_marker,
                        bbox=_union([ln.bbox] + [l.bbox for l in item_lines[1:]]),
                    )
                )
                i = j
                in_list = True
                continue
            # paragraph
            group = [ln]
            j = i + 1
            while j < n:
                nxt = lines[j]
                t = nxt.text.strip()
                if nxt.mono or style.heading_level(nxt.size) != level:
                    break
                m = mdtext.split_list_marker(t)
                if m and (m[0] == "glyph" or mdtext.ends_sentence(group[-1].text)):
                    break
                if self._paragraph_break(group[-1], nxt, left, right):
                    break
                group.append(nxt)
                j += 1
            para = self._paragraph(group, left, right)
            if len(group) == 1 and not level and ln.size >= 0.95 * style.body_size and _is_bold_title(ln):
                # A lone bold line at body size ("3.1 Traces") may be a sub-heading;
                # assemble() decides once it can see what follows.
                sub = min(len(style.heading_sizes) + 1, 6)
                body = render_spans(_join_line_spans(group, style.vocab), plain=True).strip()
                frags.append(Frag("title?", body, bbox=ln.bbox, level=sub, alt=para))
            else:
                frags.append(Frag("para", para, bbox=_union([l.bbox for l in group])))
            i = j
            in_list = False
        return frags

    def _paragraph_break(self, prev: Line, nxt: Line, left: float, right: float) -> bool:
        size = max(prev.size, nxt.size, 1)
        gap = nxt.bbox[1] - prev.bbox[3]
        if gap > 0.7 * size:
            return True
        width = max(right - left, 1)
        indent = nxt.x0 - left
        prev_ended = mdtext.ends_sentence(prev.text)
        if indent > 0.8 * size and prev.x0 - left < 0.3 * size and prev_ended and width > 20 * size:
            return True
        if prev_ended and (right - prev.bbox[2]) > 0.3 * width and width > 20 * size:
            return True
        return False

    def _paragraph(self, lines: List[Line], left: float, right: float) -> str:
        width = max(right - left, 1)
        if len(lines) >= 3:
            inner = lines[:-1]
            short = sum(1 for l in inner if (right - l.bbox[2]) > 0.2 * width)
            if short >= 2 and short / len(inner) >= 0.5:
                # Ragged lines (verse, addresses): keep the line breaks.
                return "\n".join(
                    mdtext.escape_line_start(render_spans(_join_line_spans([l], self.style.vocab)).strip())
                    for l in lines
                )
        body = render_spans(_join_line_spans(lines, self.style.vocab)).strip()
        return mdtext.escape_line_start(body)

    def _code(self, lines: List[Line]) -> str:
        widths = []
        for l in lines:
            for s in l.spans:
                if len(s.text) >= 2:
                    widths.append((s.bbox[2] - s.bbox[0]) / len(s.text))
        cw = statistics.median(widths) if widths else 6.0
        base = min(l.x0 for l in lines)
        out = []
        prev = None
        for l in lines:
            if prev is not None:
                gap = l.bbox[1] - prev.bbox[3]
                blanks = int(gap / max(prev.bbox[3] - prev.bbox[1], 1) + 0.25)
                out.extend([""] * min(blanks, 3))
            indent = int(round((l.x0 - base) / cw)) if cw > 0 else 0
            out.append(" " * max(indent, 0) + l.text.rstrip())
            prev = l
        body = "\n".join(out)
        fence = mdtext.fence_for(body)
        return f"{fence}\n{body}\n{fence}"

    # ---- fragments -> markdown
    def assemble(self, frags: List[Frag]) -> str:
        blocks: List[str] = []
        list_lines: List[str] = []
        stack: List[tuple] = []  # (x0, child indent)

        def flush():
            if list_lines:
                blocks.append("\n".join(list_lines))
                list_lines.clear()
            stack.clear()

        frags = [Frag(**vars(f)) for f in frags]
        for i, f in enumerate(frags):
            if f.kind == "title?":
                nxt = frags[i + 1] if i + 1 < len(frags) else None
                f.kind = "heading" if _introduces(f, nxt) else "para"
                if f.kind == "para":
                    f.text = f.alt
        merged: List[Frag] = []
        for f in frags:
            p = merged[-1] if merged else None
            if (
                p is not None
                and f.kind == p.kind == "heading"
                and f.level == p.level
                and f.bbox and p.bbox
                and 0 <= f.bbox[1] - p.bbox[3] < 0.6 * (p.bbox[3] - p.bbox[1])
                and f.bbox[0] < p.bbox[2] and p.bbox[0] < f.bbox[2]
            ):
                p.text = mdtext.join_two(p.text, f.text, self.style.vocab)
                p.bbox = _union([p.bbox, f.bbox])
                continue
            merged.append(Frag(**vars(f)))
        for f in merged:
            if f.kind == "heading":
                if len(f.text) > MAX_HEADING_CHARS:
                    f.kind = "para"  # several heading-sized blocks that together are prose
                    f.text = mdtext.escape_line_start(f.text)
                else:
                    f.text = "#" * f.level + " " + f.text
        frags = merged

        for f in frags:
            if f.kind == "list":
                while stack and f.x0 < stack[-1][0] - 2:
                    stack.pop()
                if stack and abs(f.x0 - stack[-1][0]) <= 2:
                    stack.pop()
                indent = stack[-1][1] if stack else ""
                list_lines.append(f"{indent}{f.marker} {f.text}".rstrip())
                child = len(f.marker.split(" ")[0]) + 1
                stack.append((f.x0, indent + " " * child))
                continue
            flush()
            blocks.append(f.text)
        flush()
        blocks = mdtext.merge_continuations([b for b in blocks if b.strip()], self.style.vocab)
        return "\n\n".join(blocks)


# ------------------------------------------------------ tables & images

def _may_have_table(page) -> bool:
    """A ruled table needs some straight rules or boxes; most text pages have none."""
    try:
        paths = page.get_cdrawings()
    except Exception:
        return True
    rules = 0
    for path in paths:
        for it in path.get("items", ()):
            if it[0] in ("re", "qu"):
                rules += 4
            elif it[0] == "l":
                (x0, y0), (x1, y1) = it[1], it[2]
                if abs(x0 - x1) < 1 or abs(y0 - y1) < 1:
                    rules += 1
            if rules >= 3:
                return True
    return False


def _find_tables(page) -> List[tuple]:
    """(bbox, markdown) for each plausible ruled table on the page."""
    out = []
    if not _may_have_table(page):
        return out
    try:
        found = page.find_tables()
    except Exception:
        return out
    for tab in getattr(found, "tables", []):
        try:
            rows = tab.extract()
        except Exception:
            continue
        rows = [[mdtext.join_lines(mdtext.normalize_text(c or "").splitlines()) for c in r] for r in rows]
        header = None
        try:
            if tab.header.external:
                header = [mdtext.normalize_text(c or "") for c in tab.header.names]
        except Exception:
            pass
        if header:
            rows = [header] + rows
        if not rows:
            continue
        ncols = max(len(r) for r in rows)
        rows = [r + [""] * (ncols - len(r)) for r in rows]
        keep = [c for c in range(ncols) if any(r[c].strip() for r in rows)]
        rows = [[r[c] for c in keep] for r in rows]
        rows = [r for r in rows if any(c.strip() for c in r)]
        if len(rows) < 2 or len(keep) < 2:
            continue
        cells = [c for r in rows for c in r]
        if sum(1 for c in cells if not c.strip()) > 0.6 * len(cells):
            continue
        md = ["| " + " | ".join(mdtext.escape_table_cell(c) for c in rows[0]) + " |"]
        md.append("|" + "|".join(" --- " for _ in rows[0]) + "|")
        for r in rows[1:]:
            md.append("| " + " | ".join(mdtext.escape_table_cell(c) for c in r) + " |")
        out.append((tuple(tab.bbox), "\n".join(md)))
    return out


def _inside(inner, outer, frac: float = 0.6) -> bool:
    ix0, iy0 = max(inner[0], outer[0]), max(inner[1], outer[1])
    ix1, iy1 = min(inner[2], outer[2]), min(inner[3], outer[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return False
    area = max((inner[2] - inner[0]) * (inner[3] - inner[1]), 1e-6)
    return (ix1 - ix0) * (iy1 - iy0) / area >= frac


class AssetWriter:
    """Saves images next to the Markdown file and returns relative links."""

    def __init__(self, directory, link_prefix: str):
        self.directory = directory
        self.link_prefix = link_prefix.rstrip("/")
        self.count = 0

    def save(self, name: str, data: bytes) -> str:
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / name).write_bytes(data)
        self.count += 1
        return quote(f"{self.link_prefix}/{name}" if self.link_prefix else name)


def _image_items(page, assets: Optional[AssetWriter], skip_xrefs: set, figures: List[tuple]) -> List[Item]:
    if assets is None:
        return []
    items = []
    page_area = page.rect.width * page.rect.height or 1
    try:
        infos = page.get_image_info(xrefs=True)
    except Exception:
        return items
    k = 0
    seen = set()
    for info in infos:
        bbox = fitz.Rect(info["bbox"]) & page.rect
        if bbox.is_empty or bbox.width < 24 or bbox.height < 24:
            continue
        if bbox.width * bbox.height < 0.005 * page_area:
            continue
        xref = info.get("xref", 0)
        if xref and xref in skip_xrefs:
            continue
        if any(_inside(tuple(bbox), f, 0.8) for f in figures):
            continue
        key = (xref, tuple(round(v) for v in bbox))
        if key in seen:
            continue
        seen.add(key)
        k += 1
        data, ext = None, None
        if xref:
            try:
                img = page.parent.extract_image(xref)
                if img and not img.get("smask") and img.get("ext") in ("png", "jpeg", "jpg") and img.get("colorspace", 3) in (1, 3):
                    data, ext = img["image"], img["ext"]
            except Exception:
                data = None
        if data is None:
            pix = page.get_pixmap(clip=bbox, dpi=150, alpha=False)
            data, ext = pix.tobytes("png"), "png"
        link = assets.save(f"page{page.number + 1:03d}-img{k}.{ext}", data)
        items.append(Item("image", tuple(bbox), markdown=f"![Page {page.number + 1}, image {k}]({link})"))
    return items


def _figure_rects(page, tables: List[tuple], text_lines: List[Line]) -> List[tuple]:
    """Areas of vector drawings that form a figure (charts, diagrams)."""
    try:
        drawings = page.get_drawings()
    except Exception:
        return []
    if len(drawings) < 6:
        return []
    try:
        clusters = page.cluster_drawings(drawings=drawings)
    except Exception:
        return []
    page_area = page.rect.width * page.rect.height or 1
    out = []
    for r in clusters:
        r = fitz.Rect(r) & page.rect
        area = r.width * r.height
        if r.width < 40 or r.height < 40 or area < 0.02 * page_area or area > 0.85 * page_area:
            continue
        if any(_inside(tuple(r), t[0], 0.3) or _inside(t[0], tuple(r), 0.5) for t in tables):
            continue
        # Straight lines have zero-height rects, which never "intersect".
        paths = sum(1 for d in drawings if (fitz.Rect(d["rect"]) + (-1, -1, 1, 1)).intersects(r))
        if paths < 6:
            continue
        text_area = 0.0
        for l in text_lines:
            if _inside(l.bbox, tuple(r), 0.8):
                text_area += (l.bbox[2] - l.bbox[0]) * (l.bbox[3] - l.bbox[1])
        if text_area / area > 0.35:
            continue  # a shaded box around text, not a picture
        out.append(tuple(r))
    return out


# ------------------------------------------------------------ top level

def page_markdown(
    page,
    data: PageData,
    style: DocStyle,
    assets: Optional[AssetWriter] = None,
    skip_xrefs: Optional[set] = None,
    image_ocr: Optional[Callable] = None,
) -> str:
    """Markdown for one digital page.

    image_ocr(page, rect) -> str, when given, transcribes a large embedded
    picture (a photo of handwritten notes pasted into a typed document); the
    transcription follows the image.
    """
    renderer = PageRenderer(style)
    tables = _find_tables(page)
    all_lines = [l for b in data.lines for l in b]
    figures = _figure_rects(page, tables, all_lines) if assets is not None else []

    items: List[Item] = []
    for block in _merge_bullets(data.lines):
        kept = []
        for ln in block:
            if any(_inside(ln.bbox, t[0], 0.5) for t in tables):
                continue
            if any(_inside(ln.bbox, f, 0.9) for f in figures) and len(ln.text.strip()) <= 40:
                continue  # labels drawn inside a figure are in its picture
            kept.append(ln)
        if kept:
            items.append(Item("text", _union([l.bbox for l in kept]), lines=kept))
    for bbox, md in tables:
        items.append(Item("table", bbox, markdown=md))
    fig_n = 0
    for rect in figures:
        fig_n += 1
        pix = page.get_pixmap(clip=fitz.Rect(rect) + (-2, -2, 2, 2), dpi=150, alpha=False)
        link = assets.save(f"page{page.number + 1:03d}-fig{fig_n}.png", pix.tobytes("png"))
        items.append(Item("image", rect, markdown=f"![Page {page.number + 1}, figure {fig_n}]({link})"))
    images = _image_items(page, assets, skip_xrefs or set(), figures)
    text_chars = sum(len(l.text.strip()) for l in all_lines)
    page_area = page.rect.width * page.rect.height or 1
    for it in images:
        area = (it.bbox[2] - it.bbox[0]) * (it.bbox[3] - it.bbox[1])
        if image_ocr is not None and area >= 0.4 * page_area and text_chars < 300:
            text = (image_ocr(page, fitz.Rect(it.bbox)) or "").strip()
            if text:
                it.markdown += "\n\n" + text
    items.extend(images)

    ordered = reading_order(items, key=lambda it: it.bbox, nlines=lambda it: max(len(it.lines), 1))
    # Footnotes (small print low on the page) go last, so a paragraph they
    # interrupt at a column break can be joined back together.
    notes = [it for it in ordered if _is_footnote(it, style, page.rect.height)]
    if notes:
        ordered = [it for it in ordered if it not in notes] + notes
    frags: List[Frag] = []
    for it in ordered:
        if it.kind == "text":
            frags.extend(renderer.block_fragments(it.lines))
        else:
            frags.append(Frag(it.kind, it.markdown))
    return renderer.assemble(frags)


def _is_footnote(item: Item, style: DocStyle, page_height: float) -> bool:
    if item.kind != "text" or item.bbox[1] < 0.55 * page_height:
        return False
    if any(l.size > style.body_size * 0.9 for l in item.lines):
        return False
    return not mdtext.is_caption(item.lines[0].text)


def _merge_bullets(blocks: List[List[Line]]) -> List[List[Line]]:
    """Attach a bullet glyph that sits alone (often its own block) to its text."""
    all_lines = [(bi, li, l) for bi, b in enumerate(blocks) for li, l in enumerate(b)]
    remove = set()
    for bi, li, l in all_lines:
        if not mdtext.is_bullet_glyph(l.text):
            continue
        cy = (l.bbox[1] + l.bbox[3]) / 2
        best = None
        for bj, lj, m in all_lines:
            if (bj, lj) == (bi, li) or (bj, lj) in remove or mdtext.is_bullet_glyph(m.text):
                continue
            h = max(m.bbox[3] - m.bbox[1], 1)
            mcy = (m.bbox[1] + m.bbox[3]) / 2
            dx = m.bbox[0] - l.bbox[2]
            if abs(mcy - cy) <= 0.5 * h and -1 <= dx <= 40:
                if best is None or dx < best[0]:
                    best = (dx, m)
        if best:
            m = best[1]
            glyph = l.text.strip()
            if mdtext.is_private_use(glyph):
                glyph = "•"
            m.spans.insert(0, Span(glyph + " ", l.bbox, l.spans[0].size))
            m.bbox = _union([m.bbox, l.bbox])
            remove.add((bi, li))
    out = []
    for bi, b in enumerate(blocks):
        kept = [l for li, l in enumerate(b) if (bi, li) not in remove]
        if kept:
            out.append(kept)
    return out


def repeated_image_xrefs(doc, page_numbers: List[int]) -> set:
    """Images (logos, letterheads) drawn on most pages: decoration, skipped."""
    if len(page_numbers) < 3:
        return set()
    counts: Counter = Counter()
    for n in page_numbers:
        try:
            xrefs = {info.get("xref", 0) for info in doc[n - 1].get_image_info(xrefs=True)}
        except Exception:
            continue
        counts.update(x for x in xrefs if x)
    need = max(3, int(len(page_numbers) * 0.5 + 0.999))
    return {x for x, c in counts.items() if c >= need}
