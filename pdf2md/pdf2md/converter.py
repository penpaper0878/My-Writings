"""Convert a document: route each page to the right reader and assemble the Markdown."""

from __future__ import annotations

import io
import os
import re
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Union

from PIL import Image, ImageOps, ImageSequence

try:
    import pymupdf as fitz
except ImportError:
    import fitz

from . import digital, mdtext
from .cache import OcrCache
from .classify import profile_page
from .ocr import EngineError, make_engine
from .ocr.base import Engine, OcrResult
from .options import Options, parse_page_spec
from .render import enhance, is_blank, render_page

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"}
_ASSET_NAME_RE = re.compile(r"^page\d{3,}-(img|fig)\d+\.(png|jpe?g)$")


class PasswordRequired(RuntimeError):
    pass


@dataclass
class PageResult:
    number: int
    kind: str  # what the page is: digital | scanned | mixed | empty
    method: str  # how it was read: text | <engine> | blank | missing | error
    markdown: str = ""
    seconds: float = 0.0
    cached: bool = False
    warnings: List[str] = field(default_factory=list)


@dataclass
class ConversionResult:
    source: Path
    output: Optional[Path]
    markdown: str
    pages: List[PageResult]
    images: int
    engine: Optional[str]
    seconds: float
    warnings: List[str]

    @property
    def incomplete(self) -> bool:
        """Some pages could not be read (no OCR engine, or it failed)."""
        return any(p.method in ("missing", "error") for p in self.pages)

    def summary(self) -> str:
        counts: Dict[str, int] = {}
        for p in self.pages:
            counts[p.method] = counts.get(p.method, 0) + 1
        label = {"text": "from text layer", "blank": "blank", "missing": "NOT transcribed", "error": "FAILED"}
        parts = [f"{n} {label.get(m, 'via ' + m)}" for m, n in counts.items()]
        cached = sum(1 for p in self.pages if p.cached)
        if cached:
            parts.append(f"{cached} from cache")
        if self.images:
            parts.append(f"{self.images} image{'s' if self.images != 1 else ''}")
        return f"{len(self.pages)} page{'s' if len(self.pages) != 1 else ''}: " + ", ".join(parts)


_EDGE_MARKUP_RE = re.compile(r"^[#>*_\s-]+|[*_|\s]+$")
_PAGE_NUMBER_RE = re.compile(r"^(page|p\.|pg\.?)?\s*\d{1,4}(\s*(of|/)\s*\d{1,4})?$", re.I)


def _edge_key(block: str) -> Optional[str]:
    text = block.strip()
    if "\n" in text or len(text) > 160:
        return None
    return re.sub(r"\s+", " ", _EDGE_MARKUP_RE.sub("", text)).lower() or None


def strip_repeated_edges(pages: List[PageResult]) -> None:
    """Drop running headers/footers that OCR transcribed on page after page.

    A line counts only if it is identical (numbers included) at the top or
    bottom of at least half the OCR'd pages, so a date or a title that changes
    from page to page is always kept. Tesseract's bare page numbers go too;
    the vision model is already told to leave them out.
    """
    if len(pages) < 4:
        return
    counts: Counter = Counter()
    for p in pages:
        blocks = mdtext.split_blocks(p.markdown)
        counts.update({k for k in map(_edge_key, blocks[:3] + blocks[-3:]) if k})
    need = max(3, -(-len(pages) // 2))
    repeated = {k for k, c in counts.items() if c >= need}

    def drop(block: str, tesseract: bool) -> bool:
        key = _edge_key(block)
        return bool(key) and (key in repeated or (tesseract and bool(_PAGE_NUMBER_RE.match(key))))

    for p in pages:
        blocks = mdtext.split_blocks(p.markdown)
        tess = p.method == "tesseract"
        n = len(blocks)
        edge = set(range(min(3, n))) | set(range(max(0, n - 3), n))
        blocks = [b for i, b in enumerate(blocks) if not (i in edge and drop(b, tess))]
        p.markdown = "\n\n".join(blocks) + "\n" if blocks else ""


def open_document(path: Path, password: Optional[str] = None):
    """Open a PDF (or a photo/scan image, turned into one page per frame)."""
    if path.suffix.lower() in IMAGE_SUFFIXES:
        doc = fitz.open()
        with Image.open(path) as im:
            for frame in ImageSequence.Iterator(im):
                frame = ImageOps.exif_transpose(frame).convert("RGB")  # phone photos: honour rotation
                buf = io.BytesIO()
                frame.save(buf, "PNG")
                # Sized so that rendering at 200 dpi gives back the original pixels.
                w, h = frame.size[0] * 72 / 200, frame.size[1] * 72 / 200
                page = doc.new_page(width=w, height=h)
                page.insert_image(page.rect, stream=buf.getvalue())
        return fitz.open("pdf", doc.tobytes())
    doc = fitz.open(path)
    if doc.needs_pass:
        if not password or not doc.authenticate(password):
            doc.close()
            raise PasswordRequired(
                f"{path.name} is password-protected; pass the password with --password"
                if not password
                else f"wrong password for {path.name}"
            )
    return doc


def _asset_location(output: Optional[Path], options: Options):
    if not options.images:
        return None
    if options.assets_dir is not None:
        directory = Path(options.assets_dir)
        if output is not None:
            prefix = os.path.relpath(directory.resolve(), output.parent.resolve())
        else:
            prefix = str(directory)
    elif output is not None:
        directory = output.parent / f"{output.stem}_assets"
        prefix = directory.name
    else:
        return None
    return directory, Path(prefix).as_posix()


class _Converter:
    def __init__(self, doc, source: Path, output: Optional[Path], options: Options, log: Callable[[str], None]):
        self.doc = doc
        self.source = source
        self.output = output
        self.options = options
        self.log = log
        self.engine: Optional[Engine] = None
        self.cache = OcrCache(options.cache_dir, options.cache)
        self.warnings: List[str] = []
        self.results: Dict[int, PageResult] = {}

    # --------------------------------------------------------------- OCR
    def _dpi(self) -> int:
        return self.options.dpi or self.engine.preferred_dpi

    def _render(self, page, clip=None) -> Image.Image:
        img = render_page(page, self._dpi(), clip=clip)
        return enhance(img) if self.options.enhance else img

    def _ocr(self, image: Image.Image, number: int):
        """(result, from_cache, seconds) for one page image; runs in a worker thread."""
        started = time.time()
        key = OcrCache.key(self.engine.signature(), image)
        hit = self.cache.get(key)
        if hit is not None:
            return hit, True, time.time() - started
        result = self.engine.transcribe(image, number)
        self.cache.put(key, result)
        return result, False, time.time() - started

    def _image_ocr(self, page, rect) -> str:
        """Transcribe a big picture inside a typed page (e.g. a photo of notes)."""
        img = self._render(page, clip=rect)
        if is_blank(img):
            return ""
        try:
            result, _, _ = self._ocr(img, page.number + 1)
        except EngineError as e:
            self.warnings.append(f"page {page.number + 1}: could not read an image: {e}")
            return ""
        self.warnings.extend(result.warnings)
        return result.markdown

    def _context_pages(self, selected: set, limit: int = 40) -> List[digital.PageData]:
        """Up to `limit` unselected pages with a text layer, spread through the document."""
        others = [n for n in range(1, self.doc.page_count + 1) if n not in selected]
        if len(others) > limit:
            step = len(others) / limit
            others = [others[int(i * step)] for i in range(limit)]
        out = []
        for n in others:
            data = digital.extract_page(self.doc[n - 1])
            if sum(len(l.text.strip()) for b in data.lines for l in b) >= 20:
                out.append(data)
        return out

    # --------------------------------------------------------------- run
    def run(self) -> ConversionResult:
        t0 = time.time()
        opts = self.options
        count = self.doc.page_count
        numbers = parse_page_spec(opts.pages, count) if opts.pages else list(range(1, count + 1))
        if not numbers:
            raise ValueError(f"no pages selected (the document has {count})")
        force = set(parse_page_spec(opts.ocr_pages, count)) if opts.ocr_pages else set()

        profiles = {n: profile_page(self.doc[n - 1]) for n in numbers}
        routes: Dict[int, str] = {}
        for n in numbers:
            kind = profiles[n].kind
            if kind == "empty" and n not in force:
                routes[n] = "empty"
            elif opts.mode == "ocr" or n in force:
                routes[n] = "ocr"
            elif opts.mode == "digital":
                routes[n] = "skip" if kind == "scanned" else "text"
            else:
                routes[n] = {"digital": "text", "scanned": "ocr", "mixed": "mixed"}[kind]

        wants_image_ocr = opts.mode == "auto" and any(
            routes[n] == "text" and profiles[n].largest_image >= 0.4 and profiles[n].visible_chars < 300
            for n in numbers
        )
        if any(r in ("ocr", "mixed") for r in routes.values()) or wants_image_ocr:
            self.engine = make_engine(opts, self.log)
            if self.engine:
                self.log(f"OCR engine: {self.engine.describe()}")

        for n in numbers:
            if routes[n] == "mixed":
                if self.engine and self.engine.reads_handwriting:
                    routes[n] = "ocr"
                else:
                    routes[n] = "text"
                    self.warnings.append(
                        f"page {n}: has handwriting next to typed text; only the typed text was converted "
                        "(a local vision model would read both)"
                    )

        assets = None
        location = _asset_location(self.output, opts)
        if location:
            directory, prefix = location
            if directory.is_dir():
                for old in directory.iterdir():
                    if _ASSET_NAME_RE.match(old.name):
                        old.unlink()
            assets = digital.AssetWriter(directory, prefix)

        # ---- digital pages (PyMuPDF is not thread-safe: main thread only)
        text_pages = [n for n in numbers if routes[n] == "text"]
        datas = {n: digital.extract_page(self.doc[n - 1]) for n in text_pages}
        # Typography and running headers are measured over the whole document,
        # so a page converts the same whichever pages were selected.
        context = (list(datas.values()) + self._context_pages(set(numbers))) if text_pages else []
        style = digital.DocStyle(context)
        if opts.strip_headers:
            running = digital.find_running_lines(context, style.body_size)
            for d in datas.values():
                digital.strip_running(d, running, style.body_size)
        skip_xrefs = digital.repeated_image_xrefs(self.doc, text_pages) if assets else set()
        image_ocr = self._image_ocr if (wants_image_ocr and self.engine and self.engine.reads_handwriting) else None
        for n in text_pages:
            t = time.time()
            before = len(self.warnings)
            md = digital.page_markdown(self.doc[n - 1], datas[n], style, assets, skip_xrefs, image_ocr)
            self.results[n] = PageResult(
                n, profiles[n].kind, "text", md, time.time() - t, warnings=self.warnings[before:]
            )
            if opts.verbose >= 2:
                self.log(f"page {n}: text layer")

        for n in numbers:
            if routes[n] == "empty":
                self.results[n] = PageResult(n, "empty", "blank")
            elif routes[n] == "skip":
                self.results[n] = PageResult(
                    n, profiles[n].kind, "missing",
                    f"<!-- pdf2md: page {n} is a scanned image and was not transcribed (--mode digital) -->",
                )

        # ---- OCR pages
        ocr_pages = [n for n in numbers if routes[n] == "ocr"]
        if ocr_pages and not self.engine:
            for n in ocr_pages:
                self.results[n] = PageResult(
                    n, profiles[n].kind, "missing",
                    f"<!-- pdf2md: page {n} is a scanned or handwritten page; no OCR engine was available -->",
                )
            self.warnings.append(
                f"{len(ocr_pages)} scanned/handwritten page(s) were not transcribed: no OCR engine is available. "
                "Run `pdf2md --check` to see how to set one up."
            )
        elif ocr_pages:
            self._run_ocr(ocr_pages, profiles)

        pages = [self.results[n] for n in numbers]
        if opts.strip_headers:
            strip_repeated_edges([p for p in pages if p.method not in ("text", "blank", "missing", "error")])
        for p in pages:
            for w in p.warnings:
                if w not in self.warnings:
                    self.warnings.append(w)
        if opts.page_markers:
            parts = [f"<!-- page {p.number} -->\n\n{p.markdown}".rstrip() for p in pages]
            markdown = "\n\n".join(parts).strip() + "\n"
        else:
            vocab = style.vocab | mdtext.build_vocab(p.markdown for p in pages if p.method != "text")
            markdown = mdtext.join_chunks([p.markdown for p in pages], vocab=vocab)
        if self.output is not None:
            self.output.parent.mkdir(parents=True, exist_ok=True)
            self.output.write_text(markdown, encoding="utf-8")
        return ConversionResult(
            source=self.source,
            output=self.output,
            markdown=markdown,
            pages=pages,
            images=assets.count if assets else 0,
            engine=self.engine.describe() if self.engine else None,
            seconds=time.time() - t0,
            warnings=self.warnings,
        )

    def _run_ocr(self, numbers: List[int], profiles) -> None:
        workers = self.options.workers or self.engine.default_workers
        total = len(numbers)
        done = 0
        pending: deque = deque()

        def collect(item):
            nonlocal done
            n, future = item
            try:
                result, cached, seconds = future.result()
                method = self.engine.name
            except EngineError as e:
                result = OcrResult(f"<!-- pdf2md: page {n} could not be transcribed -->", [f"page {n}: {e}"])
                cached, seconds, method = False, 0.0, "error"
            self.results[n] = PageResult(
                n, profiles[n].kind, method, result.markdown, seconds, cached, list(result.warnings)
            )
            done += 1
            note = " (cached)" if cached else f" {seconds:.1f}s"
            self.log(f"[{done}/{total}] page {n}: {profiles[n].reason} -> {method}{note}")

        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            for n in numbers:
                img = self._render(self.doc[n - 1])
                if is_blank(img):
                    self.results[n] = PageResult(n, profiles[n].kind, "blank")
                    done += 1
                    self.log(f"[{done}/{total}] page {n}: blank")
                    continue
                pending.append((n, pool.submit(self._ocr, img, n)))
                # Render ahead of the workers, but not the whole book into memory.
                while len(pending) >= workers * 2:
                    collect(pending.popleft())
            while pending:
                collect(pending.popleft())
        finally:
            # On Ctrl-C, do not sit waiting for queued pages (finished ones are cached).
            pool.shutdown(wait=not pending, cancel_futures=True)


def convert(
    source: Union[str, Path],
    output: Union[str, Path, None] = None,
    options: Optional[Options] = None,
    log: Optional[Callable[[str], None]] = None,
) -> ConversionResult:
    """Convert a PDF (or image) to Markdown.

    With output=None nothing is written and images are not extracted (unless
    options.assets_dir is set); the Markdown is in the result either way.
    """
    options = options or Options()
    options.validate()
    source = Path(source)
    out = Path(output) if output is not None else None
    doc = open_document(source, options.password)
    try:
        return _Converter(doc, source, out, options, log or (lambda _: None)).run()
    finally:
        doc.close()
