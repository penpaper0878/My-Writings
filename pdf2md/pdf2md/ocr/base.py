"""What every OCR engine provides."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from PIL import Image


class EngineUnavailable(RuntimeError):
    """The engine cannot run here (not installed, not running, model missing)."""


class EngineError(RuntimeError):
    """The engine ran but failed on a page."""


@dataclass
class OcrResult:
    markdown: str
    warnings: List[str] = field(default_factory=list)


class Engine:
    name = "engine"
    #: DPI to render pages at when the user has not chosen one.
    preferred_dpi = 200
    #: Pages worked on at once when the user has not chosen.
    default_workers = 1
    #: Whether the engine can read handwriting well.
    reads_handwriting = False

    def check(self) -> None:
        """Raise EngineUnavailable with a helpful message if the engine cannot run."""

    def signature(self) -> str:
        """Everything that changes the output, for the cache key."""
        raise NotImplementedError

    def describe(self) -> str:
        return self.name

    def transcribe(self, image: Image.Image, page_number: int) -> OcrResult:
        raise NotImplementedError
