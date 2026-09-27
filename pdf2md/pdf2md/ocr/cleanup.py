"""Tidy what a vision model returns, and notice when it has gone wrong."""

from __future__ import annotations

import re

_THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)
_OPEN_FENCE_RE = re.compile(r"^\s*```[ \t]*(markdown|md|text)?[ \t]*\n", re.I)
_CLOSE_FENCE_RE = re.compile(r"\n```\s*$")
_PREAMBLE_RE = re.compile(
    r"^(sure|certainly|of course|okay|ok|here is|here's|below is|the following is|this is)\b[^\n]{0,160}"
    r"(transcri|markdown|text|content|page|image)[^\n]*[:.]?\s*$",
    re.I,
)
_POSTAMBLE_RE = re.compile(
    r"^(let me know|i hope|feel free|if you (need|want|would)|note:|please note)\b[^\n]*$",
    re.I,
)
BLANK_MARKERS = {"[blank]", "blank", "[blank page]", "(blank)", "[empty]", "[no text]", "no text"}


def clean_model_output(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _THINK_RE.sub("", text).strip()
    lines = text.split("\n")
    if lines and _PREAMBLE_RE.match(lines[0].strip()):
        lines = lines[1:]
    while lines and (not lines[-1].strip() or _POSTAMBLE_RE.match(lines[-1].strip())):
        lines.pop()
    text = "\n".join(l.rstrip() for l in lines).strip()
    # The whole answer wrapped in a code fence.
    m = _OPEN_FENCE_RE.match(text)
    if m and _CLOSE_FENCE_RE.search(text) and text.count("```") == 2:
        text = _CLOSE_FENCE_RE.sub("", text[m.end():]).strip()
    elif m and "```" not in text[m.end():]:
        text = text[m.end():].strip()  # opened, never closed (cut off)
    if text.lower() in BLANK_MARKERS:
        return ""
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text + "\n" if text else ""


_TAIL_REPEAT_RE = re.compile(r"(.{2,200}?)(?:\s*\1){7,}\s*$", re.S)


def _tail_repeat(text: str):
    """A phrase repeated 8+ times at the very end, if it is made of words (not dots or pipes)."""
    m = _TAIL_REPEAT_RE.search(text)
    if m and len(re.findall(r"\w", m.group(1))) >= 2:
        return m
    return None


def looks_degenerate(text: str) -> bool:
    """True if the model got stuck repeating itself (a common failure on hard pages)."""
    lines = [l.strip() for l in text.split("\n")]
    run, prev = 0, None
    for l in lines:
        if not l:
            continue
        if l == prev and not re.fullmatch(r"[|\-:\s]*", l):
            run += 1
            if run >= 5:
                return True
        else:
            run, prev = 0, l
    return _tail_repeat(text[-4000:]) is not None


def collapse_repetition(text: str) -> str:
    """Keep one copy of a looping line or phrase (only used on output already judged degenerate)."""
    out, prev = [], None
    for line in text.split("\n"):
        key = line.strip()
        if key and key == prev and not re.fullmatch(r"[|\-:\s]*", key):
            continue
        out.append(line)
        if key:
            prev = key
    text = "\n".join(out)
    tail = text[-4000:]
    m = _tail_repeat(tail)
    if m:
        text = text[: len(text) - len(tail) + m.start()] + m.group(1)
    return text.rstrip() + "\n"
