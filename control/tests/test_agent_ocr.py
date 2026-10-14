"""T26: normalized qwen3 vision and qwen3 OCR in front of text-only models."""
import asyncio
import base64
import io
import json

import httpx
import pytest
from PIL import Image

from nova_control import llmkeys, main, ocr


_buffer = io.BytesIO()
Image.new("RGB", (12, 8), "white").save(_buffer, "PNG")
PNG = _buffer.getvalue()
DATA_URL = "data:image/png;base64," + base64.b64encode(PNG).decode()


def body(model="glm", url=DATA_URL):
    return {
        "model": model,
        "stream": True,
        "messages": [
            {"role": "system", "content": "keep"},
            {"role": "user", "content": [
                {"type": "text", "text": "read the image"},
                {"type": "image_url", "image_url": {"url": url}},
            ]},
        ],
    }


class FakeLLM:
    def __init__(self, response=b'{"choices":[{"message":{"content":"OCR TEXT"}}]}', error=None):
        self.response = response
        self.error = error
        self.posts = []

    async def post(self, url, headers=None, json=None, timeout=None):
        self.posts.append({"url": url, "headers": dict(headers or {}), "json": json, "timeout": timeout})
        if self.error:
            raise self.error
        return httpx.Response(200, request=httpx.Request("POST", url), content=self.response)


@pytest.fixture(autouse=True)
def clean_cache(monkeypatch):
    monkeypatch.setattr(llmkeys, "_headers", lambda: {"Authorization": "Bearer service-key"})
    asyncio.run(ocr.CACHE.clear())
    yield
    asyncio.run(ocr.CACHE.clear())


def test_qwen_gets_an_upscaled_jpeg_and_no_ocr_text():
    client = FakeLLM()
    received = body("litellm_proxy/qwen3")
    _, modified = asyncio.run(ocr.prepare_chat_completion(received, client))

    assert modified is True
    parts = received["messages"][1]["content"]
    assert len(parts) == 2
    assert parts[0] == {"type": "text", "text": "read the image"}
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "OCR TEXT" not in json.dumps(received, ensure_ascii=False)
    assert client.posts == []


def test_small_qwen_image_is_upscaled_to_2x():
    client = FakeLLM()
    received = body("qwen3")
    _, modified = asyncio.run(ocr.prepare_chat_completion(received, client))
    url = received["messages"][1]["content"][1]["image_url"]["url"]
    image = Image.open(io.BytesIO(base64.b64decode(url.partition(",")[2])))

    assert modified and image.size == (24, 16)


def test_multiple_qwen_images_keep_their_positions_and_get_no_text():
    client = FakeLLM()
    received = {
        "model": "qwen3",
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": DATA_URL}},
            {"type": "text", "text": "middle"},
            {"type": "image_url", "image_url": {"url": DATA_URL}},
        ]}],
    }
    _, modified = asyncio.run(ocr.prepare_chat_completion(received, client))
    parts = received["messages"][0]["content"]
    assert modified and len(parts) == 3
    assert parts[0]["type"] == "image_url" and parts[0]["image_url"]["url"].startswith("data:image/jpeg")
    assert parts[1] == {"type": "text", "text": "middle"}
    assert parts[2]["type"] == "image_url" and parts[2]["image_url"]["url"].startswith("data:image/jpeg")
    assert client.posts == []


def test_glm_receives_only_qwen3_ocr_text_and_no_image_part():
    client = FakeLLM()
    received = body("litellm_proxy/glm")
    _, modified = asyncio.run(ocr.prepare_chat_completion(received, client))

    assert modified is True
    # glm's message is flattened back to plain string content (the SDK sends glm strings)
    content = received["messages"][1]["content"]
    assert isinstance(content, str)
    assert content.startswith("read the image\n\n")
    assert "OCR TEXT" in content
    assert "[تصویر: 1, فقط متن OCR در دسترس است]" in content
    assert len(client.posts) == 1
    assert client.posts[0]["json"]["model"] == "qwen3"
    assert client.posts[0]["json"]["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg")
    assert client.posts[0]["headers"] == {"Authorization": "Bearer service-key"}
    assert client.posts[0]["timeout"].read == 60


def test_multiple_images_keep_their_positions_and_labels():
    client = FakeLLM()
    received = {
        "model": "glm",
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": DATA_URL}},
            {"type": "text", "text": "middle"},
            {"type": "image_url", "image_url": {"url": DATA_URL}},
        ]}],
    }
    _, modified = asyncio.run(ocr.prepare_chat_completion(received, client))
    content = received["messages"][0]["content"]
    assert modified and isinstance(content, str)
    assert content.index("[تصویر: 1,") < content.index("\n\nmiddle\n\n") < content.index("[تصویر: 2,")


def test_identical_image_bytes_hit_the_cache():
    client = FakeLLM()
    first = asyncio.run(ocr.prepare_chat_completion(body("glm"), client))
    second = asyncio.run(ocr.prepare_chat_completion(body("qwen3"), client))

    assert first[1] and second[1]
    assert len(client.posts) == 1
    assert "OCR TEXT" in first[0]["messages"][1]["content"]


def test_ocr_failure_never_removes_a_qwen_image_but_falls_back_for_glm():
    client = FakeLLM(error=asyncio.TimeoutError())
    glm = asyncio.run(ocr.prepare_chat_completion(body("glm"), client))
    qwen = asyncio.run(ocr.prepare_chat_completion(body("qwen3"), client))

    assert glm[1] is True
    assert glm[0]["messages"][1]["content"] == "read the image\n\n" + ocr.IMAGE_UNAVAILABLE

    assert qwen[1] is True
    qwen_parts = qwen[0]["messages"][1]["content"]
    assert qwen_parts[1]["type"] == "image_url"
    assert qwen_parts[1]["image_url"]["url"].startswith("data:image/jpeg")


class FakeUpstream:
    def __init__(self):
        self.status_code = 200
        self.headers = {"content-type": "text/event-stream", "transfer-encoding": "chunked"}
        self.closed = False

    def aiter_raw(self):
        async def chunks():
            yield b"data: one\n\n"
            yield b"data: two\n\n"
        return chunks()

    async def aclose(self):
        self.closed = True


class FakeHttpClient:
    def __init__(self):
        self.sent = []
        self.upstream = FakeUpstream()
        self.llm = FakeLLM()

    async def post(self, url, headers=None, json=None, timeout=None):
        return await self.llm.post(url, headers=headers, json=json, timeout=timeout)

    def build_request(self, method, url, headers=None, content=None, params=None):
        self.sent.append({"method": method, "url": url, "headers": dict(headers or {}),
                          "content": content, "params": params})
        return {"sent": self.sent[-1]}

    async def send(self, request, stream=False):
        assert stream is True
        return self.upstream


async def call_llmapi(raw, headers=None):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        return await client.post("/llmapi/v1/chat/completions", content=raw,
                                 headers=headers or {"content-type": "application/json"})


def test_streaming_is_passed_through_after_ocr_without_buffering(monkeypatch):
    fake = FakeHttpClient()
    monkeypatch.setattr(main, "_http", fake)
    response = asyncio.run(call_llmapi(json.dumps(body("glm")).encode()))

    assert response.status_code == 200
    assert b"".join(response.iter_bytes()) == b"data: one\n\ndata: two\n\n"
    assert fake.upstream.closed is True
    sent = fake.sent[0]
    assert sent["url"] == "http://litellm-internal.vllm.svc.cluster.local:4000/v1/chat/completions"
    sent_body = json.loads(sent["content"])
    assert "OCR TEXT" in sent_body["messages"][1]["content"]


def test_non_image_request_body_is_byte_identical(monkeypatch):
    fake = FakeHttpClient()
    monkeypatch.setattr(main, "_http", fake)
    raw = b'{"model":"glm", "messages":[], "stream":true}'
    response = asyncio.run(call_llmapi(raw))

    assert b"".join(response.iter_bytes()) == b"data: one\n\ndata: two\n\n"
    assert fake.sent[0]["content"] == raw
