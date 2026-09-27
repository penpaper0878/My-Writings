import base64

import pytest
from PIL import Image

import samples
from pdf2md.ocr.base import EngineUnavailable
from pdf2md.ocr.vlm import VlmEngine, build_prompt, is_local_url

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def page_image():
    img = samples.text_image(["First line of notes", "", "Second paragraph here"], size=40, height=1200)
    return img.convert("RGB")


def engine_for(server, **kw):
    e = VlmEngine(model="qwen2.5vl:7b", base_url=server.url, **kw)
    e.check()
    return e


def test_ollama_request(model_server):
    model_server.reply = lambda req, path: "# Notes\n\nFirst line of notes"
    result = engine_for(model_server).transcribe(page_image(), 1)
    assert result.markdown == "# Notes\n\nFirst line of notes\n"
    assert result.warnings == []
    path, body, _ = model_server.requests[0]
    assert path == "/api/chat"
    assert body["model"] == "qwen2.5vl:7b"
    assert body["stream"] is False
    assert body["options"]["temperature"] == 0
    # Big enough for a high-resolution page image plus a long transcription.
    assert body["options"]["num_ctx"] >= 8192
    msg = body["messages"][0]
    assert "Transcribe this page into Markdown" in msg["content"]
    assert base64.b64decode(msg["images"][0])[:8] == PNG_MAGIC


def test_chatty_answer_is_cleaned(model_server):
    model_server.reply = lambda req, path: "Here is the transcription of the page:\n\n```markdown\n# Hi\n\nText\n```"
    assert engine_for(model_server).transcribe(page_image(), 1).markdown == "# Hi\n\nText\n"


def test_blank_answer(model_server):
    model_server.reply = lambda req, path: "[blank]"
    assert engine_for(model_server).transcribe(page_image(), 1).markdown == ""


def test_looping_answer_is_retried_in_strips(model_server):
    calls = []

    def reply(req, path):
        calls.append(req)
        if len(calls) == 1:
            return "Notes\n" + "the the the the " * 60
        return f"Part {len(calls) - 1} text."

    model_server.reply = reply
    result = engine_for(model_server).transcribe(page_image(), 1)
    assert len(calls) == 3  # the failed whole page, then two strips
    assert "part 1 of 2" in calls[1]["messages"][0]["content"]
    assert "part 2 of 2" in calls[2]["messages"][0]["content"]
    assert calls[1]["options"]["repeat_penalty"] > 1
    assert result.markdown == "Part 1 text.\n\nPart 2 text.\n"
    assert result.warnings == []


def test_persistent_loop_is_collapsed_with_warning(model_server):
    model_server.reply = lambda req, path: "Real text.\n" + "\n".join(["same line"] * 30)
    result = engine_for(model_server).transcribe(page_image(), 4)
    assert "same line\nsame line" not in result.markdown
    assert "Real text." in result.markdown
    assert any("page 4" in w and "repeated" in w for w in result.warnings)


def test_truncated_answer_is_retried(model_server):
    model_server.finish = "length"
    result = engine_for(model_server).transcribe(page_image(), 2)
    assert len(model_server.requests) == 3
    assert result.warnings  # still cut off after the retry


def test_missing_model_explains_how_to_get_it(model_server):
    model_server.models = ["llama3.2:3b"]
    with pytest.raises(EngineUnavailable, match="ollama pull qwen2.5vl:7b"):
        VlmEngine(model="qwen2.5vl:7b", base_url=model_server.url).check()


def test_model_without_vision_is_rejected(model_server):
    model_server.capabilities = ("completion",)
    with pytest.raises(EngineUnavailable, match="cannot read images"):
        VlmEngine(model="qwen2.5vl:7b", base_url=model_server.url).check()


def test_untagged_model_name_matches_latest(model_server):
    model_server.models = ["minicpm-v:latest"]
    VlmEngine(model="minicpm-v", base_url=model_server.url).check()


def test_server_not_running():
    with pytest.raises(EngineUnavailable, match="ollama serve"):
        VlmEngine(model="x", base_url="http://127.0.0.1:9").check()


def test_remote_server_refused_without_permission():
    with pytest.raises(EngineUnavailable, match="--allow-remote"):
        VlmEngine(model="x", base_url="http://8.8.8.8:11434").check()


def test_openai_compatible_server(model_server):
    model_server.models = ["olmOCR-7B-0725-Q4_K_M.gguf"]
    model_server.reply = lambda req, path: "Transcribed."
    engine = VlmEngine(model="anything", api="openai", base_url=model_server.url + "/v1")
    engine.check()
    assert engine.model == "olmOCR-7B-0725-Q4_K_M.gguf"  # a one-model server's own name
    result = engine.transcribe(page_image(), 1)
    assert result.markdown == "Transcribed.\n"
    path, body, _ = model_server.requests[0]
    assert path == "/v1/chat/completions"
    assert body["temperature"] == 0
    content = body["messages"][0]["content"]
    assert content[0]["type"] == "text"
    url = content[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1])[:8] == PNG_MAGIC


def test_api_key_is_sent(model_server):
    engine = VlmEngine(model="qwen2.5vl:7b", api="openai", base_url=model_server.url, api_key="sekret")
    engine.check()
    engine.transcribe(page_image(), 1)
    headers = model_server.requests[0][2]
    assert headers.get("Authorization") == "Bearer sekret"


def test_image_is_downscaled_to_max_side(model_server):
    engine = engine_for(model_server, max_side=512)
    engine.transcribe(Image.new("RGB", (2000, 3000), "white").convert("RGB"), 1)
    png = base64.b64decode(model_server.requests[0][1]["messages"][0]["images"][0])
    from io import BytesIO

    assert max(Image.open(BytesIO(png)).size) == 512


def test_prompt_languages_and_hint():
    prompt = build_prompt(lang="guj+hin+eng", hint="class 10 chemistry notes")
    assert "written in Gujarati, Hindi and English" in prompt
    assert "class 10 chemistry notes" in prompt
    assert "Never transliterate" in prompt
    assert "[illegible]" in prompt


@pytest.mark.parametrize(
    "url,local",
    [
        ("http://localhost:11434", True),
        ("http://127.0.0.1:8080", True),
        ("http://[::1]:11434", True),
        ("http://192.168.1.20:11434", True),
        ("http://10.0.0.5:1234", True),
        ("http://gpu-box.local:11434", True),
        ("http://8.8.8.8:11434", False),
        ("https://api.example.com", False),
    ],
)
def test_is_local_url(url, local):
    assert is_local_url(url) is local


def test_thinking_build_is_flagged_and_told_not_to_think(model_server):
    model_server.capabilities = ("completion", "vision", "thinking")
    engine = engine_for(model_server)
    assert engine.notes and "instruct" in engine.notes[0]
    engine.transcribe(page_image(), 1)
    assert model_server.requests[0][1]["think"] is False


def test_answer_lost_in_reasoning_is_not_a_blank_page(model_server):
    model_server.reply = lambda req, path: ""
    model_server.thinking = "Let me look at the page carefully... " * 50
    result = engine_for(model_server).transcribe(page_image(), 7)
    assert len(model_server.requests) == 3  # retried in strips
    assert any("page 7" in w for w in result.warnings)


def test_default_model_is_an_instruct_build():
    from pdf2md.options import DEFAULT_MODEL

    assert DEFAULT_MODEL == "qwen3-vl:8b-instruct"
