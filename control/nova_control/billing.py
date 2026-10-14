"""Dorj Agent billing edge.

nova-control never owns payment logic. It authenticates the tenant token, proxies to chat-api's
ClusterIP-only internal billing API, and reports completed company-LLM usage idempotently.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import HTTPException

from . import auth
from .config import settings
from .identity import Identity

log = logging.getLogger("nova.billing")
_queue: asyncio.Queue = asyncio.Queue()
_worker: asyncio.Task | None = None


def _headers() -> dict[str, str]:
    if not settings.agent_billing_secret:
        raise HTTPException(503, "سرویس اشتراک در دسترس نیست")
    return {"X-Agent-Billing-Secret": settings.agent_billing_secret}


def _url(path: str) -> str:
    return settings.chat_api_internal_url.rstrip("/") + path


def _clean_model(model: Any) -> str:
    """LiteLLM may receive provider-prefixed aliases; billing only knows the owner aliases."""
    return str(model or settings.default_model).split("/", 1)[-1]


async def _request(http: httpx.AsyncClient, method: str, path: str, ident: Identity, *, json_body: Any = None,
                   params: dict | None = None) -> dict:
    try:
        r = await http.request(method, _url(f"/internal/agent/{ident.slug}{path}"), headers=_headers(),
                               json=json_body, params=params)
    except httpx.HTTPError as exc:
        log.warning("agent billing API unavailable", extra={"event": "billing_unavailable", "user": ident.slug})
        raise HTTPException(503, "اتصال به سرویس پرداخت برقرار نشد") from exc
    if r.status_code in (400, 401, 402, 404, 409):
        detail = r.json().get("message") if r.headers.get("content-type", "").startswith("application/json") else ""
        raise HTTPException(r.status_code, detail or "درخواست پرداخت ناموفق بود")
    if r.status_code >= 500:
        log.warning("agent billing API failed", extra={"event": "billing_failed", "user": ident.slug,
                                                       "status": r.status_code})
        raise HTTPException(502, "سرویس پرداخت موقتا در دسترس نیست")
    try:
        value = r.json()
    except ValueError as exc:
        raise HTTPException(502, "پاسخ نامعتبر از سرویس پرداخت") from exc
    if not isinstance(value, dict):
        raise HTTPException(502, "پاسخ نامعتبر از سرویس پرداخت")
    return value


async def plans(http: httpx.AsyncClient, ident: Identity) -> dict:
    return await _request(http, "GET", "/plans", ident)


async def wallet(http: httpx.AsyncClient, ident: Identity) -> dict:
    return await _request(http, "GET", "/wallet", ident)


async def purchase(http: httpx.AsyncClient, ident: Identity, plan_id: str) -> dict:
    return await _request(http, "POST", "/purchase", ident, json_body={"plan_id": plan_id})


async def history(http: httpx.AsyncClient, ident: Identity, limit: int = 200) -> dict:
    return await _request(http, "GET", "/history", ident, params={"limit": min(max(limit, 1), 200)})


async def ensure_new_completion_allowed(http: httpx.AsyncClient, ident: Identity) -> None:
    """Admins are never blocked; enforcement defaults off until the owner enables it."""
    if not settings.agent_billing_enforce or auth.is_admin(ident):
        return
    w = await wallet(http, ident)
    if w.get("plan") in (None, "", "free") or not w.get("credit_toman"):
        raise HTTPException(402, "برای ادامه باید اشتراک دُرج ایجنت فعال باشد یا اعتبار آن را تمدید کنید")


def usage_from_payload(payload: Any) -> dict | None:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return None
    try:
        return {"in_tokens": int(usage.get("prompt_tokens") or 0),
                "out_tokens": int(usage.get("completion_tokens") or 0)}
    except (TypeError, ValueError):
        return None


def usage_from_sse(raw: bytes | str) -> dict | None:
    """Read OpenAI/LiteLLM's final stream chunk (enabled with stream_options.include_usage)."""
    text = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else raw
    found = None
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        value = line[5:].strip()
        if not value or value == "[DONE]":
            continue
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            continue
        usage = usage_from_payload(payload)
        if usage:
            found = usage
    return found


def request_id_for(request_headers: httpx.Headers | dict) -> str:
    incoming = str(dict(request_headers).get("X-Request-Id") or "").strip()
    return incoming[:120] if len(incoming) >= 8 else str(uuid.uuid4())


async def report_usage(ident: Identity, model: str, in_tokens: int, out_tokens: int, request_id: str) -> bool:
    """One idempotent report after the completion is complete. Returns False for permanent errors."""
    if not settings.agent_billing_secret:
        return False
    body = {"model": _clean_model(model), "in_tokens": max(0, int(in_tokens)),
            "out_tokens": max(0, int(out_tokens)), "request_id": request_id}
    async with httpx.AsyncClient(timeout=10) as http:
        try:
            r = await http.post(_url(f"/internal/agent/{ident.slug}/usage"), headers=_headers(), json=body)
        except httpx.HTTPError:
            log.warning("agent usage report unavailable", extra={"event": "usage_report_retry", "user": ident.slug})
            return False
        if r.status_code in (400, 401, 404):
            # A malformed/permanently rejected report would never become valid by retrying.
            log.warning("agent usage report rejected", extra={"event": "usage_report_rejected", "user": ident.slug})
            return True
        if r.status_code >= 500:
            log.warning("agent usage report failed", extra={"event": "usage_report_retry", "user": ident.slug})
            return False
        return True


async def _worker_loop() -> None:
    """Batch consumer: brief coalescing bounds calls while retry makes reports eventually durable."""
    while True:
        ident, body = await _queue.get()
        batch: list[tuple[Identity, dict]] = [(ident, body)]
        try:
            await asyncio.sleep(0.25)
            while len(batch) < 50 and not _queue.empty():
                batch.append(_queue.get_nowait())
            for item in batch:
                for attempt in range(6):
                    if await report_usage(item[0], item[1]["model"], item[1]["in_tokens"],
                                          item[1]["out_tokens"], item[1]["request_id"]):
                        break
                    await asyncio.sleep(min(30, 2 ** attempt))
        except Exception:
            log.exception("agent usage reporting worker stopped", extra={"event": "usage_report_worker_error"})
        finally:
            for _ in batch:
                _queue.task_done()


def enqueue_usage(ident: Identity, model: str, usage: dict, request_id: str) -> str:
    global _worker
    rid = request_id
    _queue.put_nowait((ident, {"model": _clean_model(model), "in_tokens": usage["in_tokens"],
                               "out_tokens": usage["out_tokens"], "request_id": rid}))
    if _worker is None or _worker.done():
        _worker = asyncio.create_task(_worker_loop())
    return rid


async def raw_teeing(stream: AsyncIterator[bytes], sink: list[bytes]) -> AsyncIterator[bytes]:
    async for chunk in stream:
        sink.append(chunk)
        yield chunk


def usage_summary(history: dict) -> dict:
    """Client-friendly period summary derived from chat-api's idempotent event ledger."""
    by_model: dict[str, dict[str, float]] = {}
    daily: dict[str, dict[str, float]] = {}
    total_in = total_out = 0
    total_cost = 0.0
    for event in history.get("usage", []):
        model = str(event.get("model") or "unknown")
        tokens_in = int(event.get("tokens_in") or 0)
        tokens_out = int(event.get("tokens_out") or 0)
        cost = float(event.get("cost_toman") or 0)
        total_in += tokens_in
        total_out += tokens_out
        total_cost += cost
        model_row = by_model.setdefault(model, {"tokens_in": 0, "tokens_out": 0, "cost_toman": 0.0})
        model_row["tokens_in"] += tokens_in
        model_row["tokens_out"] += tokens_out
        model_row["cost_toman"] += cost
        day = str(event.get("created") or "")[:10]
        day_row = daily.setdefault(day, {"tokens": 0, "cost_toman": 0.0})
        day_row["tokens"] += tokens_in + tokens_out
        day_row["cost_toman"] += cost
    return {"tokens_in": total_in, "tokens_out": total_out, "tokens": total_in + total_out,
            "cost_toman": round(total_cost, 2), "models": by_model,
            "daily": [{"day": day, **row} for day, row in sorted(daily.items())]}
