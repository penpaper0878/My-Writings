import json
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import samples  # noqa: E402

HAS_TESSERACT = shutil.which("tesseract") is not None
needs_tesseract = pytest.mark.skipif(not HAS_TESSERACT, reason="tesseract is not installed")


@pytest.fixture(scope="session")
def sample_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("samples")


@pytest.fixture(scope="session")
def digital_pdf(sample_dir):
    return samples.digital_pdf(sample_dir / "digital.pdf")


PRINTED_LINES = [
    "Chapter 4: Photosynthesis",
    "",
    "Plants convert light energy into chemical",
    "energy. The process takes place in the",
    "chloroplasts of leaf cells.",
    "",
    "- Light reactions happen in the thylakoids",
    "- The Calvin cycle fixes carbon dioxide",
]


@pytest.fixture(scope="session")
def scanned_pdf(sample_dir):
    img = samples.text_image(PRINTED_LINES, size=44)
    return samples.image_pdf(sample_dir / "scanned.pdf", [img])


class MockModelServer:
    """A stand-in for Ollama / an OpenAI-compatible server.

    `reply(request_json, path) -> str` decides the model's answer.
    """

    def __init__(self, models=("qwen2.5vl:7b", "qwen3-vl:8b-instruct"), capabilities=("completion", "vision")):
        self.requests = []
        self.models = list(models)
        self.capabilities = capabilities
        self.reply = lambda req, path: "# Page\n\nHello from the model."
        self.finish = "stop"
        self.thinking = ""  # reasoning text a "thinking" build returns next to its answer
        self.broken = False  # answer chat requests with something that is not JSON
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, obj, code=200):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/api/tags":
                    self._send({"models": [{"name": m, "model": m} for m in server.models]})
                elif self.path == "/v1/models":
                    self._send({"data": [{"id": m} for m in server.models]})
                else:
                    self._send({"error": "not found"}, 404)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/api/show":
                    self._send({"capabilities": list(server.capabilities)})
                    return
                server.requests.append((self.path, body, dict(self.headers)))
                if server.broken:
                    data = b"<html>502 Bad Gateway</html>"
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                text = server.reply(body, self.path)
                if self.path == "/api/chat":
                    message = {"role": "assistant", "content": text}
                    if server.thinking:
                        message["thinking"] = server.thinking
                    self._send({"message": message, "done_reason": server.finish})
                elif self.path == "/v1/chat/completions":
                    self._send({"choices": [{"message": {"content": text}, "finish_reason": server.finish}]})
                else:
                    self._send({"error": "not found"}, 404)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def model_server():
    server = MockModelServer()
    yield server
    server.close()
