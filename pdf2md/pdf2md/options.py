"""Conversion settings shared by the library and the command line."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

MODES = ("auto", "digital", "ocr")
ENGINES = ("auto", "vlm", "tesseract", "none")
APIS = ("ollama", "openai")

# The instruct build: Ollama's plain "qwen3-vl:<size>" tags are the thinking
# build, far slower for transcription and prone to dropping dense pages.
DEFAULT_MODEL = "qwen3-vl:8b-instruct"


def default_cache_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "pdf2md" / "cache"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "pdf2md"


@dataclass
class Options:
    # Which pages get OCR. "auto" decides per page; "digital" never OCRs;
    # "ocr" transcribes every page from its image, ignoring any text layer.
    mode: str = "auto"
    # Page spec ("3,5-7") of pages to always OCR, on top of what "auto" picks.
    ocr_pages: Optional[str] = None
    # Page spec ("1-3,10-") of pages to convert; None means all.
    pages: Optional[str] = None
    password: Optional[str] = None

    # OCR engine. "auto" uses a local vision model when one is reachable,
    # else Tesseract, else leaves a marker where the page could not be read.
    engine: str = "auto"
    model: str = DEFAULT_MODEL
    api: str = "ollama"
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    # Refuse to send pages to anything but this machine or the local network
    # unless explicitly allowed: the tool is meant to be offline.
    allow_remote: bool = False
    timeout: float = 900.0
    # Languages, as Tesseract codes joined with "+" (e.g. "eng+hin+guj").
    # Also passed to the vision model as a hint.
    lang: Optional[str] = None
    # Free-text context for the vision model, e.g. "organic chemistry notes".
    hint: Optional[str] = None
    tesseract_cmd: Optional[str] = None

    # Render resolution for OCR; None lets the engine choose (vision model 200, Tesseract 300).
    dpi: Optional[int] = None
    max_side: int = 2048
    # Split each OCR page into this many horizontal strips (cut along blank
    # rows) so dense handwriting is seen at a higher resolution.
    tiles: int = 1
    # Stretch contrast before OCR; helps faint pencil and grey photocopies.
    enhance: bool = False
    workers: int = 0  # 0 = choose per engine

    images: bool = True
    assets_dir: Optional[Path] = None
    page_markers: bool = False
    strip_headers: bool = True

    cache: bool = True
    cache_dir: Path = field(default_factory=default_cache_dir)

    verbose: int = 1  # 0 quiet, 1 progress, 2 debug

    def validate(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        if self.engine not in ENGINES:
            raise ValueError(f"engine must be one of {', '.join(ENGINES)}")
        if self.api not in APIS:
            raise ValueError(f"api must be one of {', '.join(APIS)}")
        if self.dpi is not None and not 50 <= self.dpi <= 600:
            raise ValueError("dpi must be between 50 and 600")
        if not 256 <= self.max_side <= 8192:
            raise ValueError("max_side must be between 256 and 8192")
        if not 1 <= self.tiles <= 8:
            raise ValueError("tiles must be between 1 and 8")
        if self.workers < 0:
            raise ValueError("workers cannot be negative")


def parse_page_spec(spec: str, page_count: Optional[int] = None) -> list:
    """Parse "1-3,7,10-" into sorted 1-based page numbers.

    An open range ("10-") runs to the last page, so it needs page_count.
    """
    pages = set()
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            lo_s, hi_s = part.split("-", 1)
            lo = int(lo_s) if lo_s else 1
            if hi_s:
                hi = int(hi_s)
            elif page_count is not None:
                hi = page_count
            else:
                raise ValueError(f"open page range {part!r} needs the page count")
            if lo < 1 or hi < lo:
                raise ValueError(f"bad page range {part!r}")
            pages.update(range(lo, hi + 1))
        else:
            n = int(part)
            if n < 1:
                raise ValueError(f"bad page number {part!r}")
            pages.add(n)
    if page_count is not None:
        pages = {p for p in pages if p <= page_count}
    return sorted(pages)
