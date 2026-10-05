"""Per-user workspace lifecycle on Kubernetes (the data plane).

A workspace = Secret (session key + LLM key) + PVC (home) + Deployment (agent-server, 0..1
replicas) + Service, all labelled with the user's slug in the workspace namespace. Kubernetes is
the source of truth, so nova-control stays stateless and any replica can serve any user.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from . import llmkeys
from .config import settings
from .identity import Identity
from .metrics import WS_ACTIVE, WS_EVENTS, WS_START

log = logging.getLogger("nova.workspaces")

LABEL_APP = "app.kubernetes.io/part-of"
LABEL_USER = "nova.isigpu.local/user"
ANN_EMAIL = "nova.isigpu.local/email"
ANN_ACTIVITY = "nova.isigpu.local/last-activity"
HOME = "/home/openhands"


@dataclass
class Workspace:
    slug: str
    email: str
    state: str            # missing | stopped | starting | ready | error
    replicas: int
    ready_replicas: int
    last_activity: float | None
    service_url: str

    def as_dict(self) -> dict:
        return {"user": self.email, "workspace": f"ws-{self.slug}", "state": self.state,
                "last_activity": self.last_activity, "idle_minutes_limit": settings.idle_minutes}


_loaded = False


_api_pair = None


def _apis():
    """One shared ApiClient (one urllib3 pool, kept-alive connections). A fresh ApiClient per call
    opened a new TCP+TLS connection to the API server every time - each one also a Falco "Contact
    K8S API Server" alert, i.e. a rewrite of the falco-alerts ConfigMap in etcd (2026-10-05 etcd
    NOSPACE outage)."""
    global _loaded, _api_pair
    if _api_pair is None:
        if not _loaded:
            try:
                config.load_incluster_config()
            except config.ConfigException:
                config.load_kube_config()
            _loaded = True
        cfg = client.Configuration.get_default_copy()
        cfg.connection_pool_maxsize = 32
        api = client.ApiClient(cfg)
        _api_pair = (client.CoreV1Api(api), client.AppsV1Api(api))
    return _api_pair


def name_for(slug: str) -> str:
    return f"ws-{slug}"


def service_url(slug: str) -> str:
    return f"http://{name_for(slug)}.{settings.workspace_namespace}.svc.cluster.local:{settings.agent_server_port}"


def _labels(ident: Identity) -> dict:
    return {LABEL_APP: "nova", "app.kubernetes.io/component": "workspace", LABEL_USER: ident.slug}


def _state(dep) -> str:
    if dep is None:
        return "missing"
    want = dep.spec.replicas or 0
    ready = (dep.status.ready_replicas or 0) if dep.status else 0
    if want == 0:
        return "stopped"
    if ready >= 1:
        return "ready"
    for c in (dep.status.conditions or []) if dep.status else []:
        if c.type == "Progressing" and c.status == "False":
            return "error"
    return "starting"


def _activity(dep) -> float | None:
    v = (dep.metadata.annotations or {}).get(ANN_ACTIVITY) if dep is not None else None
    try:
        return float(v) if v else None
    except ValueError:
        return None


def get(ident: Identity) -> Workspace:
    _, apps = _apis()
    try:
        dep = apps.read_namespaced_deployment(name_for(ident.slug), settings.workspace_namespace)
    except ApiException as e:
        if e.status != 404:
            raise
        dep = None
    return Workspace(ident.slug, ident.email, _state(dep), (dep.spec.replicas or 0) if dep else 0,
                     (dep.status.ready_replicas or 0) if dep and dep.status else 0, _activity(dep), service_url(ident.slug))


def session_key(ident: Identity) -> str:
    core, _ = _apis()
    s = core.read_namespaced_secret(name_for(ident.slug), settings.workspace_namespace)
    return base64.b64decode(s.data["session-api-key"]).decode()


def _tenant_token(ident: Identity) -> str:
    from . import tenants

    c = tenants.by_name(ident.slug)
    return c["token"] if c else ""


def llm_key(ident: Identity) -> str | None:
    """The user's LiteLLM virtual key (made with the workspace); None before the first workspace."""
    core, _ = _apis()
    try:
        s = core.read_namespaced_secret(name_for(ident.slug), settings.workspace_namespace)
    except ApiException as e:
        if e.status == 404:
            return None
        raise
    return base64.b64decode(s.data["llm-api-key"]).decode()


def _deployment(ident: Identity) -> client.V1Deployment:
    n = name_for(ident.slug)

    def from_secret(var, key):
        return client.V1EnvVar(name=var, value_from=client.V1EnvVarSource(
            secret_key_ref=client.V1SecretKeySelector(name=n, key=key)))

    env = [
        from_secret("SESSION_API_KEY", "session-api-key"),
        client.V1EnvVar(name="HOME", value=HOME),
        client.V1EnvVar(name="OPENHANDS_SUPPRESS_BANNER", value="1"),
        # NovaAgent reverse workspace: the agent's file/terminal tools run on the customer's PC through
        # the gateway (sidecar_bridge); /c/<name>/v1 only reaches this customer's own connection
        client.V1EnvVar(name="NOVAAGENT_LOCAL_TOOLS_URL", value=f"{settings.gateway_url}/c/{ident.slug}"),
        from_secret("NOVAAGENT_CUSTOMER_WORKSPACE_TOKEN", "tenant-token"),
        client.V1EnvVar(name="NOVAAGENT_TENANT", value=ident.slug),
        # the app's company-managed LLM profile names the public /llmapi; in-cluster it goes straight to LiteLLM
        client.V1EnvVar(name="NOVA_LLM_PUBLIC_BASE", value=f"{settings.public_url}/llmapi"),
        client.V1EnvVar(name="NOVA_LLM_INTERNAL_BASE", value=settings.litellm_url),
        client.V1EnvVar(name="NOVA_REASONING_EFFORT_MAP", value=settings.reasoning_effort_map),
    ]
    probe = client.V1Probe(http_get=client.V1HTTPGetAction(path="/alive", port=settings.agent_server_port),
                           period_seconds=5, failure_threshold=60)
    container = client.V1Container(
        name="agent-server", image=settings.agent_server_image, image_pull_policy="IfNotPresent",
        working_dir=HOME,   # agent-server keeps conversations/settings under ./workspace -> on the PVC
        ports=[client.V1ContainerPort(container_port=settings.agent_server_port, name="http")],
        env=env, startup_probe=probe,
        readiness_probe=client.V1Probe(http_get=client.V1HTTPGetAction(path="/alive", port=settings.agent_server_port), period_seconds=10),
        resources=client.V1ResourceRequirements(
            requests={"cpu": "250m", "memory": "1Gi"},
            limits={"cpu": settings.workspace_cpu, "memory": settings.workspace_memory}),
        volume_mounts=[client.V1VolumeMount(name="home", mount_path=HOME)],
        # the image declares USER openhands by name; kubelet needs the numeric id to verify non-root
        security_context=client.V1SecurityContext(allow_privilege_escalation=False, run_as_non_root=True,
                                                  run_as_user=10001, run_as_group=10001,
                                                  capabilities=client.V1Capabilities(drop=["ALL"])),
    )
    pod = client.V1PodSpec(
        containers=[container], automount_service_account_token=False, enable_service_links=False,
        security_context=client.V1PodSecurityContext(fs_group=10001, seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault")),
        volumes=[client.V1Volume(name="home", persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(claim_name=n))],
    )
    return client.V1Deployment(
        metadata=client.V1ObjectMeta(name=n, labels=_labels(ident),
                                     annotations={ANN_EMAIL: ident.email, ANN_ACTIVITY: str(time.time())}),
        spec=client.V1DeploymentSpec(
            replicas=1, strategy=client.V1DeploymentStrategy(type="Recreate"),
            selector=client.V1LabelSelector(match_labels={LABEL_USER: ident.slug, "app.kubernetes.io/component": "workspace"}),
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(labels=_labels(ident), annotations={
                    "sidecar.istio.io/inject": "false",
                    "nova.isigpu.local/tenant-token-sha": hashlib.sha256(_tenant_token(ident).encode()).hexdigest()[:16]}),
                spec=pod)))


async def _create(ident: Identity) -> None:
    core, apps = _apis()
    ns, n = settings.workspace_namespace, name_for(ident.slug)
    llm_key = await llmkeys.create_user_key(ident.email, ident.slug)
    secret = client.V1Secret(metadata=client.V1ObjectMeta(name=n, labels=_labels(ident)),
                             string_data={"session-api-key": secrets.token_urlsafe(32), "llm-api-key": llm_key,
                                          "tenant-token": _tenant_token(ident)})
    pvc = client.V1PersistentVolumeClaim(
        metadata=client.V1ObjectMeta(name=n, labels=_labels(ident)),
        spec=client.V1PersistentVolumeClaimSpec(access_modes=["ReadWriteOnce"], storage_class_name=settings.storage_class,
                                                resources=client.V1VolumeResourceRequirements(requests={"storage": settings.workspace_storage})))
    svc = client.V1Service(metadata=client.V1ObjectMeta(name=n, labels=_labels(ident)),
                           spec=client.V1ServiceSpec(selector={LABEL_USER: ident.slug, "app.kubernetes.io/component": "workspace"},
                                                     ports=[client.V1ServicePort(name="http", port=settings.agent_server_port,
                                                                                 target_port=settings.agent_server_port)]))
    for create, body in ((core.create_namespaced_secret, secret), (core.create_namespaced_persistent_volume_claim, pvc),
                         (core.create_namespaced_service, svc), (apps.create_namespaced_deployment, _deployment(ident))):
        try:
            await asyncio.to_thread(create, ns, body)
        except ApiException as e:
            if e.status != 409:      # already exists (concurrent request or partial earlier run)
                raise
    WS_EVENTS.labels("created").inc()
    log.info("workspace created", extra={"user": ident.email, "workspace": n, "event": "created"})


async def sync_tenant_token(ident: Identity) -> None:
    """Keep the workspace Secret's tenant-token equal to the registry (first rollout, rotation)."""
    core, _ = _apis()
    tok = _tenant_token(ident)
    sec = await asyncio.to_thread(core.read_namespaced_secret, name_for(ident.slug), settings.workspace_namespace)
    if base64.b64decode((sec.data or {}).get("tenant-token", "") or b"").decode() != tok:
        await asyncio.to_thread(core.patch_namespaced_secret, name_for(ident.slug), settings.workspace_namespace,
                                {"stringData": {"tenant-token": tok}})


async def _apply_template(ident: Identity) -> None:
    await sync_tenant_token(ident)
    _, apps = _apis()
    want = _deployment(ident)
    body = {"spec": {"template": client.ApiClient().sanitize_for_serialization(want.spec.template)}}
    await asyncio.to_thread(apps.patch_namespaced_deployment, name_for(ident.slug), settings.workspace_namespace, body)


async def _scale(slug: str, replicas: int) -> None:
    _, apps = _apis()
    await asyncio.to_thread(apps.patch_namespaced_deployment_scale, name_for(slug), settings.workspace_namespace,
                            {"spec": {"replicas": replicas}})


_locks: dict[str, asyncio.Lock] = {}


_drift_checked: dict[str, float] = {}


def _drifted(ident: Identity) -> bool:
    """Running workspace still on an older template (image) or an old tenant token (rotation)."""
    _, apps = _apis()
    dep = apps.read_namespaced_deployment(name_for(ident.slug), settings.workspace_namespace)
    tpl = dep.spec.template
    want_sha = hashlib.sha256(_tenant_token(ident).encode()).hexdigest()[:16]
    return (tpl.spec.containers[0].image != settings.agent_server_image
            or (tpl.metadata.annotations or {}).get("nova.isigpu.local/tenant-token-sha") != want_sha)


async def ensure_ready(ident: Identity) -> Workspace:
    """Create or wake the user's workspace and wait until it answers. Single-flight per user."""
    lock = _locks.setdefault(ident.slug, asyncio.Lock())
    async with lock:
        ws = await asyncio.to_thread(get, ident)
        if ws.state == "ready":
            if time.monotonic() - _drift_checked.get(ident.slug, 0) < 60:
                return ws
            _drift_checked[ident.slug] = time.monotonic()
            if not await asyncio.to_thread(_drifted, ident):
                return ws
            log.info("workspace template changed; rolling", extra={"workspace": name_for(ident.slug), "event": "rollout"})
            ws = Workspace(ws.slug, ws.email, "starting", ws.replicas, 0, ws.last_activity, ws.service_url)
        t0, kind = time.monotonic(), "wake"
        if ws.state != "missing":
            await _touch_slug(ident.slug, force=True)
        if ws.state == "missing":
            kind = "create"
            await _create(ident)
            await sync_tenant_token(ident)      # Secret may pre-date the gateway (409 on create)
        else:
            # roll the user's workspace onto the current template (image, limits, security) whenever
            # it is not ready - this is how existing workspaces pick up a nova-control upgrade
            await _apply_template(ident)
            await asyncio.sleep(3)    # let the Recreate rollout take the old pod down before polling readiness
            if ws.replicas == 0:
                await _scale(ident.slug, 1)
                WS_EVENTS.labels("woken").inc()
        deadline = t0 + settings.start_timeout_s
        while time.monotonic() < deadline:
            ws = await asyncio.to_thread(get, ident)
            if ws.state == "ready":
                await seed_llm_settings(ident)   # idempotent: no-op when already pointing at the gateway
                WS_START.labels(kind).observe(time.monotonic() - t0)
                return ws
            if ws.state == "error":
                break
            await asyncio.sleep(1.5)
        WS_EVENTS.labels("start_failed").inc()
        return ws


async def seed_llm_settings(ident: Identity, model: str | None = None) -> None:
    """Point the new workspace's agent at the gateway with the user's own virtual key. The key is
    stored server-side by agent-server (masked on read, encrypted for the UI)."""
    import httpx
    core, _ = _apis()
    sec = await asyncio.to_thread(core.read_namespaced_secret, name_for(ident.slug), settings.workspace_namespace)
    key = base64.b64decode(sec.data["llm-api-key"]).decode()
    sess = base64.b64decode(sec.data["session-api-key"]).decode()
    hdr = {"X-Session-API-Key": sess}
    async with httpx.AsyncClient(timeout=30) as c:
        cur = await c.get(f"{service_url(ident.slug)}/api/settings", headers=hdr)
        llm = (cur.json().get("agent_settings") or {}).get("llm") or {} if cur.status_code < 300 else {}
        if not model and llm.get("base_url") == settings.litellm_url and llm.get("api_key"):
            return
        body = {"agent_settings_diff": {"llm": {"model": f"litellm_proxy/{model or settings.default_model}",
                                                "base_url": settings.litellm_url, "api_key": key}}}
        r = await c.patch(f"{service_url(ident.slug)}/api/settings", json=body, headers=hdr)
    if r.status_code >= 300:
        log.error("seeding LLM settings failed", extra={"workspace": name_for(ident.slug), "event": "seed_failed"})
        WS_EVENTS.labels("seed_failed").inc()


async def restart(ident: Identity) -> None:
    await sync_tenant_token(ident)
    _, apps = _apis()
    want = _deployment(ident)
    want.spec.template.metadata.annotations["kubectl.kubernetes.io/restartedAt"] = datetime.now(timezone.utc).isoformat()
    body = {"spec": {"template": client.ApiClient().sanitize_for_serialization(want.spec.template)}}
    await asyncio.to_thread(apps.patch_namespaced_deployment, name_for(ident.slug), settings.workspace_namespace, body)
    _drift_checked.pop(ident.slug, None)
    WS_EVENTS.labels("restarted").inc()


async def stop(ident: Identity) -> None:
    await _scale(ident.slug, 0)
    WS_EVENTS.labels("stopped").inc()


_last_touch: dict[str, float] = {}


async def touch(ident: Identity) -> None:
    """Record activity on the Deployment (throttled to once a minute per user per replica)."""
    await _touch_slug(ident.slug)


async def _touch_slug(slug: str, force: bool = False) -> None:
    now = time.time()
    if not force and now - _last_touch.get(slug, 0) < 60:
        return
    _last_touch[slug] = now
    _, apps = _apis()
    try:
        await asyncio.to_thread(apps.patch_namespaced_deployment, name_for(slug), settings.workspace_namespace,
                                {"metadata": {"annotations": {ANN_ACTIVITY: str(now)}}})
    except ApiException:
        pass


async def reconcile_forever(local_active) -> None:
    """Scale idle workspaces to zero and publish per-state gauges. Runs in every replica; the
    operations are idempotent, so no leader election is needed for v1.

    ``local_active()`` -> slugs with an open UI socket or customer tunnel on *this* replica. Those
    are touched first, so the shared last-activity annotation keeps a customer's workspace up for
    as long as their app is open, whichever replica holds the connection."""
    while True:
        try:
            for slug in local_active():
                await _touch_slug(slug)
            _, apps = _apis()
            deps = await asyncio.to_thread(apps.list_namespaced_deployment, settings.workspace_namespace,
                                           label_selector=f"{LABEL_APP}=nova,app.kubernetes.io/component=workspace")
            counts = {"ready": 0, "starting": 0, "stopped": 0, "error": 0}
            now = time.time()
            for d in deps.items:
                st = _state(d)
                counts[st] = counts.get(st, 0) + 1
                slug = d.metadata.labels.get(LABEL_USER)
                act = _activity(d) or 0
                if st == "ready" and now - act > settings.idle_minutes * 60:
                    await _scale(slug, 0)
                    WS_EVENTS.labels("idle_stopped").inc()
                    log.info("workspace idle, scaled to zero", extra={"workspace": d.metadata.name, "event": "idle_stopped"})
            for k, v in counts.items():
                WS_ACTIVE.labels(k).set(v)
        except Exception:  # keep the loop alive whatever happens
            log.exception("reconcile failed")
        await asyncio.sleep(60)


def utc_iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None
