"""Image preparation and OCR on the agent-server LiteLLM path.

Dorj Agent serves qwen3 and glm.  qwen3 is vision-capable, but glm is not; the
proxy therefore sends qwen3 an upscaled JPEG image and replaces images on glm
with text extracted by qwen3.  A small in-memory LRU is essential because an
OpenHands conversation sends its whole history again on every step.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import io
import json
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import httpx

from . import llmkeys
from .config import settings
from .metrics import OCR_EVENTS, OCR_LATENCY

log = logging.getLogger("nova.control.ocr")

# Same extraction contract as Dorj Chat.  qwen3 beat PaddleOCR-VL on the Persian
# table benchmark, so it is the OCR engine as well as the vision model.
PROMPT = ("تمام متن این تصویر را کامل و دقیق و بدون ترجمه استخراج کن. جدول‌ها را با همه ردیف‌ها و ستون‌ها به صورت جدول "
          "markdown بنویس. اعداد را رقم به رقم دقیق بخوان. هیچ توضیح یا مقدمه‌ای اضافه نکن")
OCR_PREFIX = "متن استخراج‌شده از تصویر (OCR):\n"
IMAGE_UNAVAILABLE = "[تصویر قابل خواندن نبود]"

# Small screenshots are upscaled (max 2x); this was the qwen3 Persian-table win
# measured during the chat rollout (43/56 cells at 1x -> 55/56 at 2x).
TARGET_SIDE = 2400
MAX_UPSCALE = 2.0


@dataclass(frozen=True)
class OcrResult:
    text: str
    ok: bool
    cache_hit: bool = False


@dataclass(frozen=True)
class PreparedImage:
    """An upscaled data URL (when readable) plus the original bytes for cache keying."""
    vision_url: str | None
    image_bytes: bytes | None


class OcrCache:
    """Async-safe byte-hash -> extraction result cache (TTL + LRU, no image bytes)."""

    def __init__(self, max_entries: int = 2000, ttl_seconds: int = 24 * 60 * 60):
        self.max_entries = max(1, max_entries)
        self.ttl_seconds = ttl_seconds
        self._items: OrderedDict[str, tuple[float, OcrResult]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> OcrResult | None:
        async with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            stored_at, result = item
            if time.monotonic() - stored_at >= self.ttl_seconds:
                del self._items[key]
                return None
            self._items.move_to_end(key)
            return OcrResult(result.text, result.ok, True)

    async def put(self, key: str, text: str, ok: bool) -> None:
        async with self._lock:
            self._items[key] = (time.monotonic(), OcrResult(text, ok, False))
            self._items.move_to_end(key)
            while len(self._items) > self.max_entries:
                self._items.popitem(last=False)

    async def clear(self) -> None:
        async with self._lock:
            self._items.clear()


CACHE = OcrCache()


def _image_model_names(raw: str | None = None) -> set[str]:
    return {item.strip().lower() for item in (raw if raw is not None else settings.image_models).split(",") if item.strip()}


def served_model(model: Any) -> str:
    """LiteLLM/OpenHands may prefix the served name (litellm_proxy/qwen3)."""
    return str(model or "").rsplit("/", 1)[-1].strip().lower()


def supports_images(body: dict, image_models: set[str] | None = None) -> bool:
    models = image_models if image_models is not None else _image_model_names()
    return served_model(body.get("model")) in models


def _decode_data_url(url: str) -> bytes:
    header, _, data = url.partition(",")
    if not _ or not header.startswith("data:"):
        raise ValueError("invalid data URL")
    if ";base64" not in header.lower():
        raise ValueError("non-base64 data URL")
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid base64 image") from exc


def _image_url(part: dict) -> str | None:
    value = part.get("image_url")
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        url = value.get("url")
        return str(url) if isinstance(url, str) else None
    return None


def _content_text(data: dict) -> str:
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        text = "\n".join(
            str(part.get("text") or "")
            for part in content if isinstance(part, dict) and part.get("type") == "text"
        ).strip()
        if text:
            return text
    raise ValueError("no OCR content")


async def _load_image(url: str, client: httpx.AsyncClient) -> bytes:
    if url.startswith("data:"):
        return _decode_data_url(url)
    # Only inline images: fetching agent-supplied http(s) URLs from inside the cluster would be an SSRF path.
    raise ValueError("unsupported image URL")


def _jpeg_data_url(img) -> str:
    """Copy of Dorj Chat's measured image normalization for qwen3."""
    from PIL import Image
    img = img.convert("RGB")
    scale = min(TARGET_SIDE / max(img.size), MAX_UPSCALE)
    if abs(scale - 1) > 0.05:
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


_OCR_SEMAPHORE = asyncio.Semaphore(settings.ocr_concurrency)


async def _prepare_image(url: str, client: httpx.AsyncClient) -> PreparedImage:
    """Load and normalize an image; errors never include image content."""
    image_bytes: bytes | None = None
    try:
        image_bytes = await _load_image(url, client)
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        return PreparedImage(_jpeg_data_url(img), image_bytes)
    except Exception as exc:
        log.warning("agent image normalization failed exception=%s", type(exc).__name__)
        return PreparedImage(None, image_bytes)


async def _extract_image(part: dict, client: httpx.AsyncClient) -> tuple[OcrResult, str | None]:
    """Extract text for a text-only model; return the result and normalized URL."""
    url = _image_url(part)
    if not url:
        OCR_EVENTS.labels("error").inc()
        return OcrResult(IMAGE_UNAVAILABLE, False), None
    async with _OCR_SEMAPHORE:
        started = time.perf_counter()
        try:
            prepared = await _prepare_image(url, client)
            if not prepared.vision_url or prepared.image_bytes is None:
                raise ValueError("unreadable image")
            digest = hashlib.sha256(prepared.image_bytes).hexdigest()
            cached = await CACHE.get(digest)
            if cached is not None:
                OCR_LATENCY.observe(time.perf_counter() - started)
                OCR_EVENTS.labels("hit").inc()
                return cached, prepared.vision_url
            headers = llmkeys._headers()
            response = await client.post(
                f"{settings.litellm_url.rstrip('/')}/v1/chat/completions",
                headers=headers,
                json={
                    "model": settings.image_text_model,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": PROMPT},
                            {"type": "image_url", "image_url": {"url": prepared.vision_url}},
                        ],
                    }],
                    "temperature": 0,
                    "max_tokens": 4096,
                },
                timeout=httpx.Timeout(settings.ocr_timeout_seconds, connect=10),
            )
            response.raise_for_status()
            text = _content_text(response.json())
            await CACHE.put(digest, text, True)
            OCR_LATENCY.observe(time.perf_counter() - started)
            OCR_EVENTS.labels("miss").inc()
            return OcrResult(text, True), prepared.vision_url
        except Exception as exc:
            OCR_LATENCY.observe(time.perf_counter() - started)
            OCR_EVENTS.labels("error").inc()
            # Never include an image/data URL or exception payload; it may contain image content.
            log.warning("agent OCR failed model=%s exception=%s", settings.image_text_model, type(exc).__name__)
            return OcrResult(IMAGE_UNAVAILABLE, False), None


async def _vision_image(part: dict, client: httpx.AsyncClient) -> str | None:
    """Normalize an image for qwen3 without adding any OCR text."""
    url = _image_url(part)
    if not url:
        return None
    async with _OCR_SEMAPHORE:
        prepared = await _prepare_image(url, client)
        return prepared.vision_url


def _replacement_text(result: OcrResult, label: str) -> str:
    if not result.ok:
        return IMAGE_UNAVAILABLE
    suffix = f"[تصویر: {label}, فقط متن OCR در دسترس است]"
    return f"{OCR_PREFIX}{result.text}\n{suffix}"


def _set_image_url(part: dict, url: str) -> None:
    if isinstance(part.get("image_url"), dict):
        part["image_url"]["url"] = url
    else:
        part["image_url"] = url


async def prepare_chat_completion(body: dict, client: httpx.AsyncClient) -> tuple[dict, bool]:
    """Modify a chat completion in place and report whether the outbound body changed.

    qwen3 gets the image only, but normalized/upscaled.  glm and any configured
    non-image model gets qwen3's extraction instead of the image.  Extraction
    failures are swallowed so OCR never fails the original agent request.
    """
    messages = body.get("messages")
    if not isinstance(messages, list):
        return body, False
    vision = supports_images(body)
    tasks: list[asyncio.Task] = []
    positions: list[tuple[int, int, int]] = []
    for message_pos, message in enumerate(messages):
        if not isinstance(message, dict) or not isinstance(message.get("content"), list):
            continue
        for part_pos, part in enumerate(message["content"]):
            if isinstance(part, dict) and part.get("type") == "image_url":
                image_number = len(positions) + 1
                if vision:
                    tasks.append(asyncio.create_task(_vision_image(part, client)))
                else:
                    tasks.append(asyncio.create_task(_extract_image(part, client)))
                positions.append((message_pos, part_pos, image_number))

    if not tasks:
        return body, False
    results = await asyncio.gather(*tasks)

    modified = False
    hits = misses = failures = 0
    # Descending order keeps later image positions valid when qwen's image URLs are
    # replaced in-place and older positions are processed safely.
    ordered = sorted(zip(positions, results), key=lambda item: item[0][:2], reverse=True)
    for (message_pos, part_pos, image_number), result in ordered:
        message = messages[message_pos]
        if not isinstance(message, dict) or not isinstance(message.get("content"), list):
            continue
        part = message["content"][part_pos]
        if not isinstance(part, dict):
            continue
        label = str(part.get("name") or part.get("filename") or image_number)
        if vision:
            vision_url = result
            if vision_url:
                _set_image_url(part, vision_url)
                modified = True
            continue
        ocr_result, _vision_url = result
        message["content"][part_pos] = {"type": "text", "text": _replacement_text(ocr_result, label)}
        modified = True
        hits += int(ocr_result.cache_hit)
        misses += int(ocr_result.ok and not ocr_result.cache_hit)
        failures += int(not ocr_result.ok)
    if not vision:
        log.info("agent OCR images=%d cache_hits=%d misses=%d failures=%d vision=%s",
                 len(results), hits, misses, failures, vision)
    return body, modified
