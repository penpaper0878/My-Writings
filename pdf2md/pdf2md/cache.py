"""On-disk cache of OCR results, so re-running a long notebook costs nothing.

Keyed on the exact page pixels plus everything about the engine that changes
its output (model, prompt version, languages, …). Interrupt a conversion and
run it again: the pages already done come straight back.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from PIL import Image

from .ocr.base import OcrResult


class OcrCache:
    def __init__(self, directory: Path, enabled: bool = True):
        self.directory = Path(directory)
        self.enabled = enabled

    @staticmethod
    def key(signature: str, image: Image.Image) -> str:
        h = hashlib.sha256()
        h.update(signature.encode("utf-8"))
        h.update(f"{image.mode}{image.size}".encode())
        h.update(image.tobytes())
        return h.hexdigest()

    def _path(self, key: str) -> Path:
        return self.directory / key[:2] / f"{key}.json"

    def get(self, key: str) -> Optional[OcrResult]:
        if not self.enabled:
            return None
        try:
            data = json.loads(self._path(key).read_text(encoding="utf-8"))
            return OcrResult(data["markdown"], list(data.get("warnings", [])))
        except (OSError, ValueError, KeyError):
            return None

    def put(self, key: str, result: OcrResult) -> None:
        if not self.enabled:
            return
        path = self._path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"markdown": result.markdown, "warnings": result.warnings}, f, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError:
            pass  # a cache that cannot be written is only slower
