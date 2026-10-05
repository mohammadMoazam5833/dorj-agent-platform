"""NovaAgent admin panel on Kubernetes: NovaAgent's own panel UI (scripts/admin/nova-admin.mjs, the PAGE
it serves, lightly adapted by adminpage.py) with the same JSON API, re-implemented against Kubernetes:

  tenants   = customer registry entries (one per AD user) + their agent-server workspace
  services  = Deployments in the nova namespace (+ each customer's ws-<name>)
  logs      = pod logs;  disk = workspace PVCs;  LLM = our LiteLLM;  metrics = Prometheus

Sign-in is a realm-"nova" user in group nova-admins (not a shared panel password); session = HMAC
cookie, 12 h, like the original.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from kubernetes import client
from kubernetes.client.rest import ApiException

from . import adminpage, auth, tenants, workspaces
from .config import settings
from .identity import Identity

log = logging.getLogger("nova.admin")
router = APIRouter(prefix="/admin")
COOKIE = "na_admin"
CORE_DEPLOYMENTS = ["nova-control", "nova-gateway", "nova-downloads"]
_fails: dict[str, tuple[int, float]] = {}


# ── session ──────────────────────────────────────────────────────────────────────────────────────

def _sign(v: str) -> str:
    return hmac.new(settings.admin_secret.encode(), v.encode(), hashlib.sha256).hexdigest()


def _cookie_ok(request: Request) -> str | None:
    raw = request.cookies.get(COOKIE, "")
    try:
        exp, user, sig = raw.split(".", 2)
        if int(exp) > time.time() and hmac.compare_digest(sig, _sign(f"{exp}.{user}")):
            return user
    except ValueError:
        pass
    return None


def _need(request: Request) -> str:
    user = _cookie_ok(request)
    if not user:
        raise HTTPException(401, "unauthorized")
    return user


@router.get("", include_in_schema=False)
@router.get("/", include_in_schema=False)
async def page():
    return HTMLResponse(adminpage.render(), headers={"Cache-Control": "no-store"})


@router.post("/api/login")
async def login(request: Request):
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "")
    n, t = _fails.get(ip, (0, 0.0))
    if n >= 10 and time.time() - t < 300:
        return JSONResponse({"error": "تلاش زیاد؛ ۵ دقیقه دیگر دوباره امتحان کنید"}, status_code=429)
    body = await request.json()
    try:
        cust = await auth.password_login(str(body.get("username") or "").strip(), str(body.get("password") or ""))
    except auth.LoginFailed:
        _fails[ip] = (n + 1, time.time())
        return JSONResponse({"error": "نام کاربری یا گذرواژه نادرست است"}, status_code=401)
    if settings.admin_group not in cust.get("groups", []):
        log.warning("admin login by non-admin", extra={"user": cust["email"], "event": "admin_denied"})
        return JSONResponse({"error": "این حساب دسترسی مدیریت ندارد"}, status_code=403)
    _fails.pop(ip, None)
    exp = str(int(time.time()) + 12 * 3600)
    val = f"{exp}.{cust['name']}"
    resp = JSONResponse({"ok": True})
    resp.set_cookie(COOKIE, f"{val}.{_sign(val)}", max_age=12 * 3600, httponly=True, secure=True, samesite="strict", path="/admin")
    log.info("admin login", extra={"user": cust["email"], "event": "admin_login"})
    return resp


@router.get("/api/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/admin")
    return resp


# ── helpers ──────────────────────────────────────────────────────────────────────────────────────

def _apis():
    return workspaces._apis()


async def _prom(q: str) -> list:
    try:
        async with httpx.AsyncClient(timeout=6) as c:
            r = await c.get(f"{settings.prometheus_url}/api/v1/query", params={"query": q})
        return r.json().get("data", {}).get("result", []) if r.status_code == 200 else []
    except httpx.HTTPError:
        return []


def _num(res: list, default: float = 0.0) -> float:
    try:
        return float(res[0]["value"][1])
    except (IndexError, KeyError, ValueError):
        return default


async def _gateway() -> dict:
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            return (await c.get(f"{settings.gateway_url}/health")).json()
    except (httpx.HTTPError, ValueError):
        return {}


def _deployments() -> list[tuple[str, str, object]]:
    _, apps = _apis()
    out = []
    for ns in (settings.control_namespace, settings.workspace_namespace):
        for d in apps.list_namespaced_deployment(ns).items:
            if ns == settings.control_namespace and d.metadata.name not in CORE_DEPLOYMENTS:
                continue
            out.append((ns, d.metadata.name, d))
    return out


def _uptime(ns: str, dep) -> int | None:
    core, _ = _apis()
    sel = ",".join(f"{k}={v}" for k, v in (dep.spec.selector.match_labels or {}).items())
    starts = [p.status.start_time.timestamp() for p in core.list_namespaced_pod(ns, label_selector=sel).items
              if p.status and p.status.start_time and p.status.phase == "Running"]
    return int(time.time() - min(starts)) if starts else None


def _active(dep) -> str:
    if (dep.spec.replicas or 0) == 0:
        return "inactive"
    return "active" if (dep.status.ready_replicas or 0) > 0 else "activating"


# ── API (same shapes as nova-admin.mjs) ──────────────────────────────────────────────────────────

@router.get("/api/overview")
async def overview(request: Request):
    _need(request)
    deps = await asyncio.to_thread(_deployments)
    services = []
    for ns, name, d in deps:
        services.append({"unit": name, "active": _active(d), "upSec": await asyncio.to_thread(_uptime, ns, d)})
    nss = f'namespace=~"{settings.control_namespace}|{settings.workspace_namespace}"'
    mem_used, mem_lim, cpu_used, cpu_lim, pvc_cap, pvc_used = await asyncio.gather(
        _prom(f'sum(container_memory_working_set_bytes{{{nss},container!=""}})'),
        _prom(f'sum(kube_pod_container_resource_limits{{{nss},resource="memory"}})'),
        _prom(f'sum(rate(container_cpu_usage_seconds_total{{{nss},container!=""}}[5m]))'),
        _prom(f'sum(kube_pod_container_resource_limits{{{nss},resource="cpu"}})'),
        _prom(f'sum(kubelet_volume_stats_capacity_bytes{{namespace="{settings.workspace_namespace}"}})'),
        _prom(f'sum(kubelet_volume_stats_used_bytes{{namespace="{settings.workspace_namespace}"}})'))
    gw = await _gateway()
    up = next((s["upSec"] for s in services if s["unit"] == "nova-control"), 0) or 0
    total = _num(mem_lim)
    return {"services": services, "uptime": up, "uptimeText": _fmt_up(up),
            "mem": {"total": total, "free": max(total - _num(mem_used), 0)},
            "load": [round(_num(cpu_used), 2)] * 3, "cpuCount": int(_num(cpu_lim) or 1),
            "disk": {"total": _num(pvc_cap), "used": _num(pvc_used)},
            "gateway": {"customers": [{"name": c.get("name"), "connected": bool(c.get("connected"))} for c in gw.get("customers", [])]},
            "hostname": f"Kubernetes · {settings.control_namespace}", "now": int(time.time() * 1000)}


def _fmt_up(s: int) -> str:
    d, h, m = s // 86400, s % 86400 // 3600, s % 3600 // 60
    return (f"{d} روز " if d else "") + f"{h} ساعت {m} دقیقه"


async def _conv_list(ident: Identity, limit: int) -> list | None:
    ws = await asyncio.to_thread(workspaces.get, ident)
    if ws.state != "ready":
        return None
    key = await asyncio.to_thread(workspaces.session_key, ident)
    try:
        async with httpx.AsyncClient(timeout=6) as c:
            r = await c.get(f"{ws.service_url}/api/conversations/search", params={"limit": limit}, headers={"X-Session-API-Key": key})
        return r.json().get("items", []) if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


@router.get("/api/tenants")
async def tenants_list(request: Request):
    _need(request)
    gw = {c.get("name"): bool(c.get("connected")) for c in (await _gateway()).get("customers", [])}
    sizes = {r["metric"].get("persistentvolumeclaim"): float(r["value"][1]) for r in
             await _prom(f'kubelet_volume_stats_used_bytes{{namespace="{settings.workspace_namespace}"}}')}
    out = []
    for c in await asyncio.to_thread(tenants.all_customers, True):
        ident = tenants.identity_of(c)
        ws = await asyncio.to_thread(workspaces.get, ident)
        convs = await _conv_list(ident, 100)
        size = sizes.get(workspaces.name_for(c["name"]))
        out.append({"name": c["name"], "email": c.get("email", ""), "port": "ws-" + c["name"],
                    "home": f"{settings.workspace_namespace}/{workspaces.name_for(c['name'])}",
                    "roots": c.get("roots", []), "unit": workspaces.name_for(c["name"]),
                    "active": "active" if ws.state == "ready" else ("inactive" if ws.state in ("stopped", "missing") else ws.state),
                    "agentOk": convs is not None, "clientConnected": gw.get(c["name"], False),
                    "convCount": len(convs) if convs is not None else None, "size": size,
                    "sizeText": _human(size), "token": (c["token"][:6] + "…") if c.get("token") else "",
                    "hasPortalUser": True, "disabled": bool(c.get("disabled"))})
    return {"tenants": out}


def _human(b) -> str:
    if b is None:
        return "-"
    for u in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.0f} {u}" if u == "B" else f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} PB"


def _tenant(name: str) -> dict:
    c = tenants.by_name(name)
    if not c:
        raise HTTPException(404, "tenant پیدا نشد")
    return c


@router.post("/api/tenants/{name}/action")
async def tenant_action(name: str, request: Request):
    who = _need(request)
    action = str((await request.json()).get("action") or "")
    ident = tenants.identity_of(_tenant(name))
    if action == "start":
        ws = await workspaces.ensure_ready(ident)
    elif action == "stop":
        await workspaces.stop(ident)
        ws = await asyncio.to_thread(workspaces.get, ident)
    elif action == "restart":
        await workspaces.restart(ident)
        ws = await asyncio.to_thread(workspaces.get, ident)
    else:
        raise HTTPException(400, "bad action")
    log.info("admin tenant action", extra={"user": who, "workspace": name, "event": f"admin_{action}"})
    return {"ok": True, "unit": workspaces.name_for(name), "action": action, "state": ws.state}


@router.post("/api/tenants")
async def tenant_create(request: Request):
    who = _need(request)
    name = str((await request.json()).get("name") or "").strip().lower()
    if not name or len(name) > 64:
        raise HTTPException(400, "نام کاربری AD را وارد کنید")
    email = name if "@" in name else f"{name}@{settings.email_domain}"
    c = await asyncio.to_thread(tenants.ensure_for, email)
    asyncio.create_task(workspaces.ensure_ready(tenants.identity_of(c)))
    log.info("admin tenant create", extra={"user": who, "workspace": c["name"], "event": "admin_create"})
    return {"ok": True, "name": c["name"], "port": "ws-" + c["name"], "token": "", "home": "", "portalUser": True}


@router.post("/api/tenants/{name}/password")
async def tenant_password(name: str, request: Request):
    _need(request)
    return JSONResponse({"error": "گذرواژه‌ها در Active Directory مدیریت می‌شوند"}, status_code=400)


@router.post("/api/tenants/{name}/rotate")
async def tenant_rotate(name: str, request: Request):
    who = _need(request)
    c = _tenant(name)
    await asyncio.to_thread(tenants.rotate_token, name)
    ident = tenants.identity_of(c)
    if (await asyncio.to_thread(workspaces.get, ident)).state != "missing":
        await workspaces.restart(ident)          # the agent's gateway credential changes with it
    log.info("admin token rotate", extra={"user": who, "workspace": name, "event": "admin_rotate"})
    return {"ok": True, "username": name}


@router.delete("/api/tenants/{name}")
async def tenant_delete(name: str, request: Request):
    who = _need(request)
    ident = tenants.identity_of(_tenant(name))
    if (await asyncio.to_thread(workspaces.get, ident)).state != "missing":
        await workspaces.stop(ident)
    await asyncio.to_thread(tenants.remove, name)
    log.info("admin tenant delete", extra={"user": who, "workspace": name, "event": "admin_delete"})
    return {"ok": True, "note": "فایل‌های فضای کاری (PVC) حفظ شدند"}


@router.get("/api/conversations")
async def conversations(request: Request, tenant: str = ""):
    _need(request)
    c = _tenant(tenant)
    items = await _conv_list(tenants.identity_of(c), 50)
    if items is None:
        return JSONResponse({"error": "agent-server این کاربر در حال اجرا نیست"}, status_code=502)
    rows = [{"id": cv.get("id", ""), "title": cv.get("title") or "(بدون عنوان)", "created_at": cv.get("created_at"),
             "updated_at": cv.get("updated_at") or cv.get("last_updated_at"), "status": cv.get("execution_status")} for cv in items]
    return {"tenant": tenant, "count": len(rows), "conversations": rows}


@router.get("/api/logs")
async def logs(request: Request, unit: str = "", lines: int = 100, download: str = ""):
    _need(request)
    n = min(max(lines, 10), 5000)
    deps = {name: (ns, d) for ns, name, d in await asyncio.to_thread(_deployments)}
    if unit not in deps:
        raise HTTPException(400, "bad unit")
    ns, d = deps[unit]

    def read() -> str:
        core, _ = _apis()
        sel = ",".join(f"{k}={v}" for k, v in (d.spec.selector.match_labels or {}).items())
        out = []
        for p in core.list_namespaced_pod(ns, label_selector=sel).items:
            container = p.spec.containers[0].name
            try:
                txt = core.read_namespaced_pod_log(p.metadata.name, ns, container=container, tail_lines=n, timestamps=True)
            except ApiException as e:
                txt = f"(log unavailable: {e.status})"
            out.append(f"── {p.metadata.name} ──\n{txt}")
        return "\n".join(out) or "(no running pod)"

    text = await asyncio.to_thread(read)
    if download == "1":
        return PlainTextResponse(text, headers={"Content-Disposition": f'attachment; filename="{unit}.log"'})
    return {"unit": unit, "lines": "\n".join(reversed(text.split("\n")))}


@router.get("/api/llm")
async def llm(request: Request):
    _need(request)
    hdr = {"Authorization": f"Bearer {settings.litellm_master_key}"}

    async def probe(url):
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(url, headers=hdr)
            ms = int((time.monotonic() - t0) * 1000)
            return {"ok": r.status_code == 200, "status": r.status_code, "ms": ms,
                    "models": [m["id"] for m in r.json().get("data", [])] if r.status_code == 200 else []}
        except (httpx.HTTPError, ValueError):
            return {"ok": False, "status": 0, "ms": int((time.monotonic() - t0) * 1000), "models": []}

    gw = await probe(f"{settings.litellm_url}/v1/models")
    allowed = {"ok": gw["ok"], "status": gw["status"], "ms": gw["ms"],
               "models": [m for m in gw["models"] if m in settings.allowed_models]}
    return {"local": allowed, "remote": gw}


@router.get("/api/disk-usage")
async def disk_usage(request: Request):
    _need(request)
    res = await _prom(f'kubelet_volume_stats_used_bytes{{namespace="{settings.workspace_namespace}"}}')
    items = sorted(({"dir": r["metric"].get("persistentvolumeclaim", "?"), "bytes": float(r["value"][1]),
                     "sizeText": _human(float(r["value"][1]))} for r in res), key=lambda x: -x["bytes"])
    return {"items": items}


@router.post("/api/password")
async def password(request: Request):
    _need(request)
    return JSONResponse({"error": "گذرواژه‌ی مدیران در Active Directory مدیریت می‌شود"}, status_code=400)


@router.post("/api/services/{unit}/action")
async def service_action(unit: str, request: Request):
    who = _need(request)
    action = str((await request.json()).get("action") or "")
    if unit.startswith("ws-"):
        c = tenants.by_name(unit[3:])
        if not c:
            raise HTTPException(400, "unit مجاز نیست")
        return await _ws_action(c, action, who)
    if unit not in CORE_DEPLOYMENTS or action != "restart":
        return JSONResponse({"error": "برای سرویس‌های اصلی فقط ری‌استارت مجاز است"}, status_code=400)
    _, apps = _apis()
    await asyncio.to_thread(apps.patch_namespaced_deployment, unit, settings.control_namespace,
                            {"spec": {"template": {"metadata": {"annotations": {
                                "kubectl.kubernetes.io/restartedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}}}}})
    log.info("admin service restart", extra={"user": who, "event": "admin_restart", "workspace": unit})
    return {"ok": True, "state": "restarting"}


async def _ws_action(c: dict, action: str, who: str):
    ident = tenants.identity_of(c)
    if action == "start":
        await workspaces.ensure_ready(ident)
    elif action == "stop":
        await workspaces.stop(ident)
    elif action == "restart":
        await workspaces.restart(ident)
    else:
        raise HTTPException(400, "bad action")
    log.info("admin workspace action", extra={"user": who, "workspace": c["name"], "event": f"admin_{action}"})
    return {"ok": True, "state": (await asyncio.to_thread(workspaces.get, ident)).state}


@router.post("/api/system/reboot")
async def reboot(request: Request):
    _need(request)
    return JSONResponse({"error": "ری‌استارت سرور در Kubernetes معنا ندارد؛ سرویس‌ها را جداگانه ری‌استارت کنید"}, status_code=400)

