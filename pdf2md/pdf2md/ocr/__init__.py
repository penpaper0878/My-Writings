"""OCR engines and how one is chosen."""

from __future__ import annotations

from typing import Callable, Optional

from .base import Engine, EngineError, EngineUnavailable, OcrResult
from .tesseract import TesseractEngine
from .vlm import VlmEngine

__all__ = ["Engine", "EngineError", "EngineUnavailable", "OcrResult", "make_engine"]


def make_engine(options, log: Callable[[str], None] = lambda _: None) -> Optional[Engine]:
    """The engine to use, or None if none can run.

    "auto" prefers a local vision model (handwriting, tables, maths), then
    Tesseract (printed text only). An engine asked for by name must work, or
    EngineUnavailable explains how to fix it.
    """
    if options.engine == "none":
        return None
    if options.engine in ("auto", "vlm"):
        engine = VlmEngine(
            model=options.model,
            api=options.api,
            base_url=options.base_url,
            api_key=options.api_key,
            timeout=options.timeout,
            allow_remote=options.allow_remote,
            lang=options.lang,
            hint=options.hint,
            tiles=options.tiles,
            max_side=options.max_side,
        )
        try:
            engine.check()
            for note in engine.notes:
                log(f"note: {note}")
            return engine
        except EngineUnavailable as e:
            if options.engine == "vlm":
                raise
            log(f"vision model unavailable: {e}")
    if options.engine in ("auto", "tesseract"):
        engine = TesseractEngine(lang=options.lang, cmd=options.tesseract_cmd)
        try:
            engine.check()
            if options.engine == "auto":
                log("using Tesseract: fine for printed scans, weak on handwriting")
            return engine
        except EngineUnavailable as e:
            if options.engine == "tesseract":
                raise
            log(f"Tesseract unavailable: {e}")
    return None
