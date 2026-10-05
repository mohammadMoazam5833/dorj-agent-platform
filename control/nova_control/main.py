"""nova-control: NovaAgent's server edge on Kubernetes (the scripts/deploy/nova-ingress.mjs contract).

Routes
  POST /api/auth/login  desktop-client sign-in (Keycloak realm nova) -> tenant token
  /api/*, /sockets/*    the caller's own agent-server pod (HTTP + WebSocket), chosen by tenant token
  /llmapi/*             company LLM for the app's managed profile -> LiteLLM (user's own virtual key)
  /customer-workspace   reverse-workspace tunnel from the customer's PC -> gateway (WebSocket)
  /v1/*                 small JSON API (me, workspace, models, usage) for the CLI / admin
  /admin, /admin/api/*  NovaAgent's admin panel (realm-nova users in group nova-admins)
  /internal/customers   gateway registry (shared bearer, never via ingress)
  /healthz /readyz      probes;  :9090/metrics  Prometheus (separate port)
  everything else       NovaAgent UI bundle for the desktop app; no token -> /download/
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

import httpx
import websockets
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from prometheus_client import start_http_server
from starlette.background import BackgroundTask

from . import __version__, admin, auth, llmkeys, tenants, workspaces
from .config import settings
from .auth import current_identity
from .identity import Identity
from .metrics import HTTP_LATENCY, HTTP_REQUESTS, LOGINS, PROXY_LATENCY, TUNNELS, WS_SOCKETS
from .telemetry import setup_logging, setup_tracing

setup_logging()
log = logging.getLogger("nova.control")
app = FastAPI(title="nova-control", version=__version__, docs_url=None, redoc_url=None, openapi_url="/v1/openapi.json")
setup_tracing(app)
app.include_router(admin.router)     # /admin: NovaAgent's admin panel (before the SPA catch-all)

HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
              "transfer-encoding", "upgrade", "host", "content-length"}
STRIP_REQUEST = HOP_BY_HOP | {"x-session-api-key", "authorization", "cookie", "kubeflow-userid", "kubeflow-groups",
                                  "x-forwarded-email", "x-forwarded-user", "x-forwarded-groups",
                                  "x-forwarded-preferred-username", "x-forwarded-access-token"}

_sockets: collections.Counter = collections.Counter()
_tunnels: collections.Counter = collections.Counter()
_http: httpx.AsyncClient | None = None


@app.on_event("startup")
async def _startup():
    global _http
    _http = httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=600, write=60, pool=10))
    start_http_server(int(os.environ.get("NOVA_METRICS_PORT", "9090")))
    asyncio.create_task(workspaces.reconcile_forever(
        lambda: {s for s, n in (_sockets + _tunnels).items() if n > 0}))
    log.info("nova-control started", extra={"event": "startup"})


@app.middleware("http")
async def _red_metrics(request: Request, call_next):
    t0 = time.perf_counter()
    route = "login" if request.url.path == "/api/auth/login" else "llmapi" if request.url.path.startswith("/llmapi") else "proxy" if request.url.path.startswith(("/api/", "/sockets/", "/server_info", "/health", "/alive", "/ready")) and request.url.path != "/readyz" else (
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


# ── desktop sign-in (NovaAgent electron/login-prompt.html -> main.mjs loginToWorkspaceServer) ─────

@app.post("/api/auth/login")
async def login(request: Request):
    try:
        body = await request.json()
    except ValueError:
        body = {}
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    try:
        customer = await auth.password_login(username, password)
    except auth.LoginFailed as e:
        LOGINS.labels("rejected").inc()
        await asyncio.sleep(0.6)    # same pacing as the VM ingress; Keycloak brute-force protection does the rest
        return JSONResponse({"ok": False, "error": str(e)}, status_code=401)
    if customer.get("disabled"):
        return JSONResponse({"ok": False, "error": "this account is disabled; contact your administrator"}, status_code=403)
    ident = tenants.identity_of(customer)
    LOGINS.labels("ok").inc()
    asyncio.create_task(_prewarm(ident))     # start the user's agent-server while the app boots
    log.info("login ok", extra={"event": "login", "user": ident.email})
    return {"ok": True, "customer": customer["name"], "token": customer["token"]}


async def _prewarm(ident: Identity) -> None:
    try:
        await workspaces.ensure_ready(ident)
    except Exception:
        log.exception("workspace prewarm failed", extra={"user": ident.email})


@app.get("/internal/customers")
async def internal_customers(request: Request):
    # gateway registry; ingress-nginx always adds X-Forwarded-For, so this never answers from outside
    tok = request.headers.get("authorization", "")[7:]
    if request.headers.get("x-forwarded-for") or not settings.registry_token or tok != settings.registry_token:
        raise HTTPException(404)
    return await asyncio.to_thread(tenants.gateway_view)


# ── company LLM for the app's managed profile (UI: src/api/company-llm) ───────────────────────────
LLMAPI_PATHS = {"v1/models", "models", "v1/chat/completions", "chat/completions", "v1/completions",
                "completions", "v1/responses", "responses", "v1/model/info", "model/info"}


@app.api_route("/llmapi/{path:path}", methods=["GET", "POST"])
async def llmapi(path: str, request: Request):
    if path.strip("/") not in LLMAPI_PATHS:
        raise HTTPException(404)
    headers = {k: v for k, v in request.headers.items() if k.lower() in ("authorization", "content-type", "accept")}
    upstream = await _http.send(_http.build_request(request.method, f"{settings.litellm_url}/{path.strip('/')}",
                                                    headers=headers, content=request.stream(),
                                                    params=request.query_params), stream=True)
    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP}
    return StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code, headers=resp_headers,
                             background=BackgroundTask(upstream.aclose))


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
    ident = auth.identity_for(websocket)
    if ident is None:
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


# ── reverse workspace tunnel (scripts/customer-workspace/client.mjs -> gateway) ───────────────────

@app.websocket("/customer-workspace")
@app.websocket("/customer-workspace/ws")
async def customer_workspace(websocket: WebSocket):
    # the gateway authenticates the token itself; checking it here keeps junk off the gateway
    c = tenants.by_token(websocket.query_params.get("token") or websocket.headers.get("x-novaagent-local-tools-token"))
    if c is None:
        await websocket.close(code=4401)
        return
    url = settings.gateway_url.replace("http://", "ws://", 1) + "/customer-workspace?" + urlencode({"token": c["token"]})
    await websocket.accept()
    WS_SOCKETS.inc()
    TUNNELS.inc()
    _tunnels[c["name"]] += 1
    log.info("workspace tunnel up", extra={"event": "tunnel_up", "user": c["email"]})
    try:
        async with websockets.connect(url, max_size=None, open_timeout=20, ping_interval=None) as up:
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
    except (WebSocketDisconnect, websockets.ConnectionClosed, OSError):
        pass
    except (websockets.InvalidHandshake, NotImplementedError):
        # gateway refused (e.g. a brand-new customer not yet in its registry, synced every few
        # seconds): ask the app to retry instead of surfacing a 500
        try:
            await websocket.close(code=1013)
        except RuntimeError:
            pass
    finally:
        WS_SOCKETS.dec()
        TUNNELS.dec()
        _tunnels[c["name"]] -= 1
        log.info("workspace tunnel down", extra={"event": "tunnel_down", "user": c["email"]})
        try:
            await websocket.close()
        except RuntimeError:
            pass


# ── UI bundle for the desktop app (nova-ingress serveIndex) ──────────────────────────────────────

def _boot(token: str, llm_key: str, email: str) -> str:
    j = json.dumps
    session = j({"access_token": llm_key, "username": email})
    return ("<script>window.__AGENT_CANVAS_SESSION_API_KEY__=" + j(token) + ";window.__AGENT_CANVAS_AUTH_REQUIRED__=false;"
            "window.__NOVAAGENT_COMPANY_LLM_URL__=location.protocol+'//'+location.host+'/llmapi';"
            "try{localStorage.setItem('openhands-onboarded','1');"
            "localStorage.setItem('novaagent-company-llm-session'," + j(session) + ");}catch(e){}</script>")


@app.get("/{path:path}", include_in_schema=False)
async def spa(path: str, request: Request):
    root = Path(settings.web_root)
    f = (root / path).resolve()
    if path and f.is_file() and root.resolve() in f.parents and f.name != "index.html":
        cache = "public, max-age=31536000, immutable" if "/assets/" in f"/{path}" else "public, max-age=300"
        return FileResponse(f, headers={"Cache-Control": cache})
    token = auth.token_from(request)
    c = tenants.by_token(token)
    if c is None:      # NovaAgent is used from the desktop app, not as a website
        return RedirectResponse("/download/", status_code=302)
    index = root / "index.html"
    if not index.exists():
        return JSONResponse({"error": "UI bundle not installed"}, status_code=503)
    ident = tenants.identity_of(c)
    # serve the UI only once the user's agent-server answers: the app shows its loading screen meanwhile;
    # a UI that boots while the workspace is still cold-starting opens on "Manage backends" instead
    await workspaces.ensure_ready(ident)
    llm_key = await asyncio.to_thread(workspaces.llm_key, ident)
    if llm_key is None:       # first launch: the workspace (and its LLM key) is created right now
        await workspaces.ensure_ready(ident)
        llm_key = await asyncio.to_thread(workspaces.llm_key, ident) or ""
    html = index.read_text(encoding="utf-8").replace("<head>", "<head>" + _boot(token, llm_key, ident.email), 1)
    resp = HTMLResponse(html, headers={"Cache-Control": "no-store"})
    resp.set_cookie(auth.COOKIE, token, max_age=30 * 86400, httponly=True, secure=True, samesite="lax", path="/")
    return resp
