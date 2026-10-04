"""nova-control HTTP entrypoint.

Routes
  /v1/*                 control-plane API (web UI + nova CLI)
  /api/*, /sockets/*    transparent proxy to the caller's own agent-server (HTTP + WebSocket)
  /healthz /readyz      probes;  :9090/metrics  Prometheus (separate port)
  everything else       the NovaAgent SPA (static), index.html gets a non-secret bootstrap
"""
from __future__ import annotations

import asyncio
import collections
import logging
import os
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

import httpx
import websockets
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from prometheus_client import start_http_server
from starlette.background import BackgroundTask

from . import __version__, llmkeys, workspaces
from .config import settings
from .identity import Identity, current_identity, identity_from_headers
from .metrics import HTTP_LATENCY, HTTP_REQUESTS, PROXY_LATENCY, WS_SOCKETS
from .telemetry import setup_logging, setup_tracing

setup_logging()
log = logging.getLogger("nova.control")
app = FastAPI(title="nova-control", version=__version__, docs_url=None, redoc_url=None, openapi_url="/v1/openapi.json")
setup_tracing(app)

# Placeholder only: the browser needs *a* key to build its default backend; the real per-user
# session key is added server-side by the proxy and never leaves the cluster.
BROWSER_PLACEHOLDER_KEY = "nova-proxied"
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
              "transfer-encoding", "upgrade", "host", "content-length"}
STRIP_REQUEST = HOP_BY_HOP | {"x-session-api-key", "authorization", "cookie", "kubeflow-userid", "kubeflow-groups"}

_sockets: collections.Counter = collections.Counter()
_http: httpx.AsyncClient | None = None


@app.on_event("startup")
async def _startup():
    global _http
    _http = httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=600, write=60, pool=10))
    start_http_server(int(os.environ.get("NOVA_METRICS_PORT", "9090")))
    asyncio.create_task(workspaces.reconcile_forever(lambda slug: _sockets[slug] > 0))
    log.info("nova-control started", extra={"event": "startup"})


@app.middleware("http")
async def _red_metrics(request: Request, call_next):
    t0 = time.perf_counter()
    route = "proxy" if request.url.path.startswith(("/api/", "/sockets/", "/server_info", "/health", "/alive", "/ready")) and request.url.path != "/readyz" else (
        request.url.path if request.url.path.startswith("/v1/") else "static")
    code = 500
    try:
        resp = await call_next(request)
        code = resp.status_code
        return resp
    finally:
        HTTP_REQUESTS.labels(route, request.method, str(code)).inc()
        HTTP_LATENCY.labels(route).observe(time.perf_counter() - t0)


@app.exception_handler(llmkeys.LLMKeyError)
async def _llm_key_error(request: Request, exc: llmkeys.LLMKeyError):
    log.error("LLM gateway key error: %s", exc, extra={"event": "llm_key_error"})
    return JSONResponse({"error": "LLM gateway rejected the workspace key request", "detail": str(exc)[:300]}, status_code=502)


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/readyz")
async def readyz():
    return {"ok": True, "version": __version__}


# ── /v1 control-plane API ─────────────────────────────────────────────────────

@app.get("/v1/me")
async def me(ident: Identity = Depends(current_identity)):
    ws = await asyncio.to_thread(workspaces.get, ident)
    return {"email": ident.email, "user": ident.slug, "groups": list(ident.groups),
            "plan": "pro", "workspace": ws.as_dict(), "default_model": settings.default_model}


@app.get("/v1/workspace")
async def ws_status(ident: Identity = Depends(current_identity)):
    return (await asyncio.to_thread(workspaces.get, ident)).as_dict()


@app.post("/v1/workspace")
async def ws_start(ident: Identity = Depends(current_identity)):
    ws = await workspaces.ensure_ready(ident)
    if ws.state != "ready":
        raise HTTPException(503, f"workspace did not become ready (state: {ws.state})")
    return ws.as_dict()


@app.delete("/v1/workspace")
async def ws_stop(ident: Identity = Depends(current_identity)):
    await workspaces.stop(ident)
    return {"stopped": True}


@app.get("/v1/models")
async def models(ident: Identity = Depends(current_identity)):
    return {"default": settings.default_model, "models": await llmkeys.models()}


@app.get("/v1/usage")
async def usage(ident: Identity = Depends(current_identity)):
    return {"user": ident.email, "llm": await llmkeys.user_spend(ident.slug)}


# ── proxy to the caller's agent-server ───────────────────────────────────────

async def _target(ident: Identity) -> tuple[str, str]:
    ws = await workspaces.ensure_ready(ident)
    if ws.state != "ready":
        raise HTTPException(503, f"workspace is {ws.state}; try again shortly")
    await workspaces.touch(ident)
    return ws.service_url, await asyncio.to_thread(workspaces.session_key, ident)


def _query_without_key(query: str) -> str:
    return urlencode([(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if k != "session_api_key"])


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def proxy_http(path: str, request: Request, ident: Identity = Depends(current_identity)):
    base, key = await _target(ident)
    q = _query_without_key(request.url.query)
    url = f"{base}/api/{path}" + (f"?{q}" if q else "")
    headers = {k: v for k, v in request.headers.items() if k.lower() not in STRIP_REQUEST}
    headers["X-Session-API-Key"] = key
    t0 = time.perf_counter()
    upstream = await _http.send(_http.build_request(request.method, url, headers=headers, content=request.stream()), stream=True)
    PROXY_LATENCY.observe(time.perf_counter() - t0)
    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP}
    return StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code, headers=resp_headers,
                             background=BackgroundTask(upstream.aclose))


# agent-server's root-level info/health endpoints the UI calls on its backend (version check etc.)
@app.get("/server_info")
@app.get("/health")
@app.get("/alive")
@app.get("/ready")
async def proxy_root_info(request: Request, ident: Identity = Depends(current_identity)):
    base, key = await _target(ident)
    r = await _http.get(f"{base}{request.url.path}", headers={"X-Session-API-Key": key})
    return Response(r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))


@app.websocket("/sockets/{path:path}")
async def proxy_ws(path: str, websocket: WebSocket):
    try:
        ident = identity_from_headers(websocket.headers)
    except HTTPException:
        await websocket.close(code=4401)
        return
    base, key = await _target(ident)
    q = _query_without_key(websocket.url.query)
    q = (q + "&" if q else "") + urlencode({"session_api_key": key})
    url = base.replace("http://", "ws://", 1) + f"/sockets/{path}?{q}"
    await websocket.accept()
    _sockets[ident.slug] += 1
    WS_SOCKETS.inc()
    try:
        async with websockets.connect(url, additional_headers={"X-Session-API-Key": key}, max_size=None, open_timeout=20) as up:
            async def client_to_up():
                while True:
                    msg = await websocket.receive()
                    if msg["type"] == "websocket.disconnect":
                        await up.close()
                        return
                    await up.send(msg.get("text") if msg.get("text") is not None else msg.get("bytes"))

            async def up_to_client():
                async for m in up:
                    await (websocket.send_text(m) if isinstance(m, str) else websocket.send_bytes(m))

            done, pending = await asyncio.wait({asyncio.create_task(client_to_up()), asyncio.create_task(up_to_client())},
                                               return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
    except (WebSocketDisconnect, websockets.ConnectionClosed):
        pass
    finally:
        _sockets[ident.slug] -= 1
        WS_SOCKETS.dec()
        try:
            await websocket.close()
        except RuntimeError:
            pass


# ── SPA ──────────────────────────────────────────────────────────────────────

_BOOT = ("<script>window.__AGENT_CANVAS_SESSION_API_KEY__=" + repr(BROWSER_PLACEHOLDER_KEY).replace("'", '"') +
         ";window.__AGENT_CANVAS_AUTH_REQUIRED__=false;window.__NOVA__={product:'NovaAgent'};"
         "try{localStorage.setItem('openhands-onboarded','1')}catch(e){}</script>")


@app.get("/{path:path}", include_in_schema=False)
async def spa(path: str, request: Request, ident: Identity = Depends(current_identity)):
    root = Path(settings.web_root)
    f = (root / path).resolve()
    if path and f.is_file() and root.resolve() in f.parents:
        cache = "public, max-age=31536000, immutable" if "/assets/" in f"/{path}" else "public, max-age=300"
        return FileResponse(f, headers={"Cache-Control": cache})
    index = root / "index.html"
    if not index.exists():
        return JSONResponse({"error": "web UI not installed"}, status_code=503)
    html = index.read_text(encoding="utf-8").replace("<head>", "<head>" + _BOOT, 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})
