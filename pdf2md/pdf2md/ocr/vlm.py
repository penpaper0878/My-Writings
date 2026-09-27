"""Transcribe page images with a vision-language model running locally.

Two wire protocols cover practically every local runtime:

* ollama   Ollama's /api/chat (http://127.0.0.1:11434)
* openai   the OpenAI-compatible /v1/chat/completions served by llama.cpp's
           llama-server, LM Studio, vLLM, LocalAI, Jan, …

Pages never leave the machine: a server outside localhost / the private
network is refused unless explicitly allowed.
"""

from __future__ import annotations

import base64
import http.client
import ipaddress
import json
import os
import socket
import urllib.error
import urllib.request
from typing import List, Optional, Tuple
from urllib.parse import urlparse

from PIL import Image

from .. import mdtext
from ..render import fit, png_bytes, split_strips
from .base import Engine, EngineError, EngineUnavailable, OcrResult
from .cleanup import clean_model_output, collapse_repetition, looks_degenerate

PROMPT_VERSION = "3"

LANG_NAMES = {
    "eng": "English", "hin": "Hindi", "guj": "Gujarati", "mar": "Marathi", "ben": "Bengali",
    "tam": "Tamil", "tel": "Telugu", "kan": "Kannada", "mal": "Malayalam", "pan": "Punjabi",
    "ori": "Odia", "asm": "Assamese", "urd": "Urdu", "san": "Sanskrit", "nep": "Nepali",
    "fra": "French", "deu": "German", "spa": "Spanish", "ita": "Italian", "por": "Portuguese",
    "nld": "Dutch", "rus": "Russian", "ukr": "Ukrainian", "pol": "Polish", "tur": "Turkish",
    "ara": "Arabic", "fas": "Persian", "heb": "Hebrew", "ell": "Greek",
    "chi_sim": "Chinese (Simplified)", "chi_tra": "Chinese (Traditional)", "jpn": "Japanese",
    "kor": "Korean", "tha": "Thai", "vie": "Vietnamese", "ind": "Indonesian", "msa": "Malay",
}

PROMPT = """Transcribe this {what} into Markdown.

Rules:
- Copy the text exactly as written: the same words, spelling, numbers, punctuation and capitalisation. Do not correct mistakes, translate, summarise, explain or add anything.
- Keep every script as it is (Latin, Devanagari, Gujarati, Arabic, …). Never transliterate.
- Handwriting: read carefully and use the surrounding words as context. If a word cannot be read at all, write [illegible]. If you are unsure of a word, write your best reading followed by [?].
- Structure: use Markdown headings only for text that is visibly a title or heading (larger, underlined, boxed or set apart). Keep bullet and numbered lists as Markdown lists, with their nesting. Keep paragraph breaks. Use a Markdown table for anything laid out as a table.
- Use **bold** or *italic* only where the page clearly shows it. Crossed-out text: ~~text~~. Checkboxes: - [ ] and - [x].
- Maths: LaTeX, $...$ inline and $$...$$ on a line of its own. Chemical formulas, units and symbols exactly as written.
- Drawings, diagrams, charts and photos: do not describe their shapes; write one line "[Figure: short description]" where they appear, followed by any words written in them.
- Leave out page numbers, ruled lines, margins, punch holes, stains and scanner marks.
- Follow the natural reading order: top to bottom, and for side-by-side columns finish the left column before the right.
- If there is no text at all, output only: [blank]
{extra}
Output only the Markdown, with no introduction, no comments and no code fence around it."""

PART_NOTE = (
    "- This image is part {part} of {parts} of one page, cut through a blank gap. "
    "It may begin or end in the middle of a sentence, list or table; transcribe only what is visible."
)


def language_names(lang: Optional[str]) -> List[str]:
    if not lang:
        return []
    return [LANG_NAMES.get(code, code) for code in lang.replace(",", "+").split("+") if code and code != "osd"]


def build_prompt(part: int = 1, parts: int = 1, lang: Optional[str] = None, hint: Optional[str] = None) -> str:
    extra = []
    if parts > 1:
        extra.append(PART_NOTE.format(part=part, parts=parts))
    names = language_names(lang)
    if names:
        extra.append(f"- The text is written in {', '.join(names[:-1]) + ' and ' + names[-1] if len(names) > 1 else names[0]}.")
    if hint:
        extra.append(f"- About the document (use only to help read unclear words): {hint.strip()}")
    what = "page" if parts == 1 else "part of a page"
    return PROMPT.format(what=what, extra="\n".join(extra))


def is_local_url(url: str) -> bool:
    """True if the server is this machine or on a private network."""
    host = (urlparse(url).hostname or "").strip("[]").lower()
    if not host:
        return False
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local") or host.endswith(".lan"):
        return True
    try:
        addrs = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            return False
        addrs = [ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos]
    return bool(addrs) and all(a.is_loopback or a.is_private or a.is_link_local for a in addrs)


def default_base_url(api: str) -> str:
    if api == "ollama":
        host = os.environ.get("OLLAMA_HOST", "").strip()
        if host:
            if "://" not in host:
                host = "http://" + host
            u = urlparse(host)
            hostname = u.hostname or "127.0.0.1"
            if hostname in ("0.0.0.0", "::"):
                hostname = "127.0.0.1"
            return f"{u.scheme}://{hostname}:{u.port or 11434}"
        return "http://127.0.0.1:11434"
    return "http://127.0.0.1:8080"


class VlmEngine(Engine):
    name = "vlm"
    preferred_dpi = 200
    default_workers = 1
    reads_handwriting = True

    def __init__(
        self,
        model: str,
        api: str = "ollama",
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 900.0,
        allow_remote: bool = False,
        lang: Optional[str] = None,
        hint: Optional[str] = None,
        tiles: int = 1,
        max_side: int = 2048,
        max_tokens: int = 6144,
        context: int = 16384,
    ):
        self.model = model
        self.api = api
        self.base_url = (base_url or default_base_url(api)).rstrip("/")
        if self.api == "openai" and self.base_url.endswith("/v1"):
            self.base_url = self.base_url[:-3]
        self.api_key = api_key or os.environ.get("PDF2MD_API_KEY") or os.environ.get("OPENAI_API_KEY")
        self.timeout = timeout
        self.allow_remote = allow_remote
        self.lang = lang
        self.hint = hint
        self.tiles = tiles
        self.max_side = max_side
        self.max_tokens = max_tokens
        self.context = context
        self._local = is_local_url(self.base_url)
        self.thinking = False
        #: Advice about the setup, shown to the user once.
        self.notes: List[str] = []

    # ------------------------------------------------------------ plumbing
    def describe(self) -> str:
        return f"{self.model} via {self.api} at {self.base_url}"

    def signature(self) -> str:
        return json.dumps(
            ["vlm", PROMPT_VERSION, self.api, self.model, self.lang, self.hint, self.tiles, self.max_side],
            ensure_ascii=False,
        )

    def _request(self, method: str, path: str, payload=None, timeout: Optional[float] = None):
        url = self.base_url + path
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.api_key and self.api == "openai":
            req.add_header("Authorization", f"Bearer {self.api_key}")
        # A server on this machine must not be reached through an HTTP proxy.
        handlers = [urllib.request.ProxyHandler({})] if self._local else []
        opener = urllib.request.build_opener(*handlers)
        with opener.open(req, timeout=timeout or self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _guard(self) -> None:
        if not self.allow_remote and not self._local:
            raise EngineUnavailable(
                f"{self.base_url} is not on this machine or your local network. pdf2md keeps pages "
                "offline; pass --allow-remote if you really want to send them there."
            )

    def check(self) -> None:
        self._guard()
        try:
            if self.api == "ollama":
                tags = self._request("GET", "/api/tags", timeout=5)
            else:
                models = self._request("GET", "/v1/models", timeout=5)
        except urllib.error.HTTPError as e:
            raise EngineUnavailable(f"{self.base_url} answered HTTP {e.code}; is it the right server?") from e
        except (urllib.error.URLError, OSError, ValueError) as e:
            if self.api == "ollama":
                raise EngineUnavailable(
                    f"cannot reach Ollama at {self.base_url} (start it with `ollama serve` or open the Ollama app)"
                ) from e
            raise EngineUnavailable(f"cannot reach a model server at {self.base_url}") from e

        if self.api == "ollama":
            names = {m.get("name") for m in tags.get("models", [])} | {m.get("model") for m in tags.get("models", [])}
            want = self.model if ":" in self.model else self.model + ":latest"
            if want not in names and self.model not in names:
                raise EngineUnavailable(
                    f"Ollama is running but the model {self.model!r} is not installed. Run: ollama pull {self.model}"
                )
            try:
                info = self._request("POST", "/api/show", {"model": self.model}, timeout=15)
                caps = info.get("capabilities")
                if caps is not None and "vision" not in caps:
                    raise EngineUnavailable(
                        f"the Ollama model {self.model!r} cannot read images; "
                        "choose a vision model such as qwen3-vl:8b-instruct"
                    )
                if caps and "thinking" in caps:
                    self.thinking = True
                    self.notes.append(
                        f"{self.model} is a 'thinking' build: for transcription it is much slower and can "
                        "drop text on dense pages. Prefer an instruct build, e.g. qwen3-vl:8b-instruct"
                    )
            except (urllib.error.URLError, OSError, ValueError):
                pass
        else:
            ids = [m.get("id") for m in models.get("data", [])]
            if not ids:
                raise EngineUnavailable(f"the server at {self.base_url} has no model loaded")
            if self.model not in ids and len(ids) == 1:
                # llama-server and friends serve one model whatever it is called.
                self.model = ids[0]

    # ------------------------------------------------------------ reading
    def _ask(self, image: Image.Image, part: int, parts: int, retry: bool) -> Tuple[str, bool]:
        """One model call. Returns (text, stopped_early_because_of_length)."""
        img = fit(image.convert("RGB"), self.max_side)
        b64 = base64.b64encode(png_bytes(img)).decode("ascii")
        prompt = build_prompt(part, parts, self.lang, self.hint)
        try:
            if self.api == "ollama":
                options = {"temperature": 0.0, "num_ctx": self.context, "num_predict": self.max_tokens}
                if retry:
                    options.update(temperature=0.2, repeat_penalty=1.15)
                payload = {
                    "model": self.model,
                    "stream": False,
                    "keep_alive": "15m",
                    "options": options,
                    "messages": [{"role": "user", "content": prompt, "images": [b64]}],
                }
                if self.thinking:
                    payload["think"] = False  # honoured by some builds, ignored by others
                resp = self._request("POST", "/api/chat", payload)
                if resp.get("error"):
                    raise EngineError(str(resp["error"]))
                message = resp.get("message") or {}
                text = message.get("content", "")
                # Everything spent on reasoning and nothing written: a failed read, not a blank page.
                cut = resp.get("done_reason") == "length" or (not text.strip() and bool(message.get("thinking")))
            else:
                payload = {
                    "model": self.model,
                    "temperature": 0.2 if retry else 0.0,
                    "max_tokens": self.max_tokens,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                            ],
                        }
                    ],
                }
                if retry:
                    payload["frequency_penalty"] = 0.3
                resp = self._request("POST", "/v1/chat/completions", payload)
                choice = (resp.get("choices") or [{}])[0]
                text = (choice.get("message") or {}).get("content") or ""
                cut = choice.get("finish_reason") == "length"
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            raise EngineError(f"model server returned HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
            raise EngineError(f"lost contact with the model server: {e}") from e
        except ValueError as e:
            raise EngineError(f"the model server sent an unreadable answer: {e}") from e
        return clean_model_output(text), cut

    def _read(self, image: Image.Image, parts: int, retry: bool) -> Tuple[str, bool]:
        strips = split_strips(image, parts) if parts > 1 else [image]
        texts, bad = [], False
        for i, strip in enumerate(strips, 1):
            text, cut = self._ask(strip, i, len(strips), retry)
            bad = bad or cut or looks_degenerate(text)
            texts.append(text)
        return mdtext.join_chunks(texts, merge_tables="any"), bad

    def transcribe(self, image: Image.Image, page_number: int) -> OcrResult:
        self._guard()
        warnings: List[str] = []
        text, bad = self._read(image, self.tiles, retry=False)
        if bad:
            # Looping or cut off: look again in smaller pieces, gently discouraging repeats.
            parts = min(max(2, self.tiles * 2), 8)
            text2, bad2 = self._read(image, parts, retry=True)
            if not bad2:
                text, bad = text2, False
            else:
                # Both went wrong: keep whichever says more once the loops are removed.
                first, second = collapse_repetition(text), collapse_repetition(text2)
                text = second if len(second) > len(first) else first
        if bad:
            warnings.append(
                f"page {page_number}: the model repeated itself or ran out of room; "
                "the transcription may be incomplete (try --tiles 2 or a larger model)"
            )
        return OcrResult(text, warnings)
