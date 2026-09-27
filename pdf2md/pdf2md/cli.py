"""Command line: pdf2md notes.pdf [more.pdf folder/ ...]"""

from __future__ import annotations

import argparse
import os
import platform
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .options import APIS, DEFAULT_MODEL, ENGINES, MODES, Options, default_cache_dir

EXIT_OK, EXIT_ERROR, EXIT_INCOMPLETE = 0, 1, 3

EPILOG = """examples:
  pdf2md notes.pdf                       writes notes.md (+ notes_assets/ for images)
  pdf2md lecture.pdf --lang eng+hin      handwriting in English and Hindi
  pdf2md scans/ -o markdown/ -r          a whole folder, recursively
  pdf2md book.pdf --pages 1-20 -o -      first 20 pages to stdout
  pdf2md notes.pdf --mode ocr --tiles 2  re-read every page from its image, in 2 strips
  pdf2md --check                         what is installed, and what is missing

exit status: 0 ok, 1 error, 3 some pages could not be transcribed
"""


def build_parser() -> argparse.ArgumentParser:
    env = os.environ.get
    p = argparse.ArgumentParser(
        prog="pdf2md",
        description="Convert PDFs, including scanned and handwritten notes, to Markdown. Runs entirely offline.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("inputs", nargs="*", metavar="INPUT", help="PDF or image files, or folders of them")
    p.add_argument("-o", "--output", help="output .md file, or a folder; '-' for standard output")
    p.add_argument("-r", "--recursive", action="store_true", help="look for PDFs in sub-folders too")
    p.add_argument("--skip-existing", action="store_true", help="skip inputs whose .md already exists")

    g = p.add_argument_group("what to convert")
    g.add_argument("--mode", choices=MODES, default="auto",
                   help="auto: decide per page (default); digital: text layer only, never OCR; "
                        "ocr: read every page from its image")
    g.add_argument("--pages", metavar="SPEC", help="pages to convert, e.g. 1-5,8,10-")
    g.add_argument("--ocr-pages", metavar="SPEC", help="pages to always read by OCR, e.g. 3,7-9")
    g.add_argument("--password", help="password for an encrypted PDF")

    g = p.add_argument_group("OCR (scanned and handwritten pages)")
    g.add_argument("--engine", choices=ENGINES, default=env("PDF2MD_ENGINE", "auto"),
                   help="auto: local vision model if available, else Tesseract (default)")
    g.add_argument("--model", default=env("PDF2MD_MODEL", DEFAULT_MODEL),
                   help=f"vision model name (default {DEFAULT_MODEL})")
    g.add_argument("--api", choices=APIS, default=env("PDF2MD_API", "ollama"),
                   help="ollama (default) or openai: any OpenAI-compatible local server "
                        "(llama.cpp llama-server, LM Studio, vLLM)")
    g.add_argument("--base-url", default=env("PDF2MD_BASE_URL"),
                   help="model server URL (default http://127.0.0.1:11434 for ollama, :8080 for openai)")
    g.add_argument("--api-key", default=None, help="key for an OpenAI-compatible server that needs one")
    g.add_argument("--allow-remote", action="store_true",
                   help="allow a model server outside this machine/local network (off: pages stay local)")
    g.add_argument("--lang", default=env("PDF2MD_LANG"),
                   help="languages as Tesseract codes, e.g. eng, eng+hin, guj+eng (also a hint for the model)")
    g.add_argument("--hint", help='what the document is, to help read unclear words, e.g. "organic chemistry notes"')
    g.add_argument("--dpi", type=int, help="render resolution for OCR (default 200 for a model, 300 for Tesseract)")
    g.add_argument("--max-side", type=int, default=2048, help="longest image side sent to the model (default 2048)")
    g.add_argument("--tiles", type=int, default=1,
                   help="read each page in N horizontal strips; 2 helps small, dense handwriting")
    g.add_argument("--enhance", action="store_true", help="boost contrast before OCR (faint pencil, grey copies)")
    g.add_argument("--workers", type=int, default=0, help="pages read at once (default: 1 for a model)")
    g.add_argument("--timeout", type=float, default=900.0, help="seconds to wait for the model per page")
    g.add_argument("--tesseract-cmd", help="path to the tesseract executable")

    g = p.add_argument_group("output")
    g.add_argument("--no-images", action="store_true", help="do not extract images")
    g.add_argument("--assets-dir", help="where to save images (default: <output>_assets next to the .md)")
    g.add_argument("--page-markers", action="store_true", help="insert <!-- page N --> before each page")
    g.add_argument("--keep-headers", action="store_true", help="keep running headers, footers and page numbers")

    g = p.add_argument_group("other")
    g.add_argument("--no-cache", action="store_true", help="do not reuse or store OCR results")
    g.add_argument("--cache-dir", default=None, help=f"OCR cache folder (default {default_cache_dir()})")
    g.add_argument("-q", "--quiet", action="store_true", help="only print errors")
    g.add_argument("-v", "--verbose", action="store_true", help="print more detail")
    g.add_argument("--check", action="store_true", help="check the setup (PyMuPDF, vision model, Tesseract) and exit")
    g.add_argument("--version", action="version", version=f"pdf2md {__version__}")
    return p


def options_from_args(a) -> Options:
    return Options(
        mode=a.mode,
        pages=a.pages,
        ocr_pages=a.ocr_pages,
        password=a.password,
        engine=a.engine,
        model=a.model,
        api=a.api,
        base_url=a.base_url,
        api_key=a.api_key,
        allow_remote=a.allow_remote,
        timeout=a.timeout,
        lang=a.lang,
        hint=a.hint,
        tesseract_cmd=a.tesseract_cmd,
        dpi=a.dpi,
        max_side=a.max_side,
        tiles=a.tiles,
        enhance=a.enhance,
        workers=a.workers,
        images=not a.no_images,
        assets_dir=Path(a.assets_dir) if a.assets_dir else None,
        page_markers=a.page_markers,
        strip_headers=not a.keep_headers,
        cache=not a.no_cache,
        cache_dir=Path(a.cache_dir) if a.cache_dir else default_cache_dir(),
        verbose=0 if a.quiet else (2 if a.verbose else 1),
    )


def collect_inputs(inputs: List[str], recursive: bool):
    """[(file, root folder or None)] for every input, folders expanded."""
    found = []
    for raw in inputs:
        path = Path(raw)
        if path.is_dir():
            pattern = "**/*" if recursive else "*"
            files = sorted(f for f in path.glob(pattern) if f.is_file() and f.suffix.lower() == ".pdf")
            found.extend((f, path) for f in files)
        elif path.is_file():
            found.append((path, None))
        else:
            raise FileNotFoundError(f"no such file or folder: {raw}")
    return found


def output_for(src: Path, root: Optional[Path], output: Optional[str], many: bool) -> Optional[Path]:
    if output == "-":
        return None
    if output:
        out = Path(output)
        if many or out.is_dir() or output.endswith(("/", os.sep)):
            rel = src.relative_to(root) if root else Path(src.name)
            return out / rel.with_suffix(".md")
        return out
    return src.with_suffix(".md")


def check(options: Options) -> int:
    from .ocr.tesseract import TesseractEngine, installed_languages
    from .ocr.vlm import VlmEngine
    from .ocr.base import EngineUnavailable

    try:
        import pymupdf as fitz
    except ImportError:
        import fitz

    print(f"pdf2md {__version__} on Python {platform.python_version()} ({platform.system()})")
    print(f"  [ok] PyMuPDF {fitz.VersionBind}: digital PDFs are fully supported")

    vlm = VlmEngine(model=options.model, api=options.api, base_url=options.base_url,
                    api_key=options.api_key, allow_remote=options.allow_remote)
    vlm_ok = False
    try:
        vlm.check()
        vlm_ok = True
        print(f"  [ok] vision model: {vlm.describe()}  -> handwriting, scans, tables and maths")
        for note in vlm.notes:
            print(f"       note: {note}")
    except EngineUnavailable as e:
        print(f"  [--] vision model ({options.model} via {options.api}): {e}")

    tess = TesseractEngine(lang=options.lang, cmd=options.tesseract_cmd)
    tess_ok = False
    try:
        tess.check()
        tess_ok = True
        langs = ", ".join(installed_languages(tess.cmd))
        print(f"  [ok] Tesseract {tess.version} ({tess.cmd}); languages: {langs}  -> printed scans")
    except EngineUnavailable as e:
        print(f"  [--] Tesseract: {e}")

    print()
    if vlm_ok:
        print("Ready for everything, handwriting included.")
    else:
        if tess_ok:
            print("Ready for digital PDFs and printed scans. For handwriting, add a local vision model:")
        else:
            print("Ready for digital PDFs only. For scans and handwriting, add a local vision model:")
        print("  1. install Ollama from https://ollama.com (one-time download)")
        print(f"  2. ollama pull {options.model}")
        print("  3. run pdf2md again; after the download it works with no internet")
    return EXIT_OK if (vlm_ok or tess_ok) else EXIT_INCOMPLETE


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    a = parser.parse_args(argv)
    try:
        options = options_from_args(a)
        options.validate()
    except ValueError as e:
        parser.error(str(e))

    if a.check:
        return check(options)
    if not a.inputs:
        parser.error("give at least one PDF (or use --check)")

    def log(msg: str) -> None:
        if options.verbose >= 1:
            print(msg, file=sys.stderr, flush=True)

    from .converter import PasswordRequired, convert
    from .ocr.base import EngineUnavailable

    try:
        inputs = collect_inputs(a.inputs, a.recursive)
    except FileNotFoundError as e:
        print(f"pdf2md: {e}", file=sys.stderr)
        return EXIT_ERROR
    if not inputs:
        print("pdf2md: no PDF files found", file=sys.stderr)
        return EXIT_ERROR
    many = len(inputs) > 1
    if a.output == "-" and many:
        parser.error("-o - (standard output) takes a single input")

    status = EXIT_OK
    for src, root in inputs:
        out = output_for(src, root, a.output, many)
        if a.skip_existing and out is not None and out.exists():
            log(f"skip {src} ({out} exists)")
            continue
        if many or options.verbose >= 2:
            log(f"== {src}")
        try:
            result = convert(src, out, options, log=log)
        except (PasswordRequired, EngineUnavailable, ValueError) as e:
            print(f"pdf2md: {src}: {e}", file=sys.stderr)
            status = EXIT_ERROR
            continue
        except KeyboardInterrupt:
            print("\npdf2md: interrupted (pages already read are cached; run again to resume)", file=sys.stderr)
            sys.stderr.flush()
            # Exit now rather than wait for a model request that is still running.
            os._exit(130)
        except Exception as e:  # a broken PDF should not stop a batch
            if options.verbose >= 2:
                raise
            print(f"pdf2md: {src}: {type(e).__name__}: {e}", file=sys.stderr)
            status = EXIT_ERROR
            continue
        if out is None:
            sys.stdout.write(result.markdown)
            sys.stdout.flush()
        for w in result.warnings:
            log(f"warning: {w}")
        where = f" -> {out}" if out else ""
        log(f"{src.name}{where}: {result.summary()} in {result.seconds:.1f}s")
        if result.incomplete and status == EXIT_OK:
            status = EXIT_INCOMPLETE
    return status
