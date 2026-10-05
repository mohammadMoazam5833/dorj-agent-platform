"""NovaAgent server-side behaviour for agent-servers whose tools run on the customer's PC.

Loaded with ``agent-server --import-modules sidecar_bridge,nova_server_patches``. Re-implements, as
runtime wrappers around the stock 1.38.0 package, what NovaAgent's
scripts/patches/conversation_{router,service}-custhome.patch did by editing site-packages:

1. A new conversation's working_dir must lie under one of the roots the customer's Local Tools
   Sidecar exposes (``hello`` -> ``roots``); anything else (a server path, a stale path) is moved
   to the first root, so the agent never works on the server filesystem by accident.
2. Git worktrees are created on the agent-server host, which is meaningless (and fails) for a
   workspace that lives on the customer's PC, so worktree creation is skipped there and any
   worktree failure falls back to the original workspace instead of failing the conversation.

3. agent-server mkdirs (and git-inits) the conversation working_dir on its own host before
   starting. For a customer-PC path (e.g. /home/ali/project) that is not writable here and the
   start fails; when the workspace is bridged that server-side directory is irrelevant, so the
   mkdir failure is ignored (Windows paths were silently created as relative dirs on the VM), and
   the server-side "git init the workspace" step is skipped for it (the repo lives on the PC).
   Server-side reads of such a path (project skills/agents discovery) treat PermissionError as
   "not present".
   Likewise the terminal/glob/grep tool definitions refuse a working_dir that is not a directory
   *on the server*; for a bridged customer path that check is answered as "yes" (the terminal's
   commands run on the customer's PC through sidecar_bridge).
4. The NovaAgent UI's company-managed LLM profile points at the public ``https://<host>/llmapi``
   (what the customer's app can reach). Inside the cluster the same calls go straight to the
   internal LiteLLM (NOVA_LLM_PUBLIC_BASE -> NOVA_LLM_INTERNAL_BASE), same per-user key.
5. Our models do not all accept every reasoning_effort (qwen3 on vLLM rejects "high", the SDK
   default). NOVA_REASONING_EFFORT_MAP="qwen3:high=medium,..." rewrites the effort for calls to our
   LLM gateway only.

The first two are no-ops when no customer workspace is configured (plain agent-server behaviour).
"""
from __future__ import annotations

import functools
import logging
import re
import time

logger = logging.getLogger("nova_server_patches")
_WIN_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_ROOTS_TTL_S = 30.0
_roots_cache: tuple[float, list[str]] = (0.0, [])


def customer_roots() -> list[str]:
    """Roots announced by the customer's sidecar (cached briefly); [] when not connected/configured."""
    global _roots_cache
    now = time.monotonic()
    if now - _roots_cache[0] < _ROOTS_TTL_S and _roots_cache[1]:
        return _roots_cache[1]
    roots: list[str] = []
    try:
        from sidecar_bridge.client import client_from_env

        cli = client_from_env()
        if cli is not None:
            hello = cli.hello()
            raw = hello.get("roots") if isinstance(hello, dict) else None
            if isinstance(raw, list):
                roots = [str(r).rstrip("/\\") for r in raw if str(r).strip()]
    except Exception:  # customer offline: keep behaviour unchanged, the tools will report it
        logger.debug("customer roots unavailable", exc_info=True)
    _roots_cache = (now, roots)
    return roots


def _norm(p: str) -> str:
    return str(p).replace("\\", "/").rstrip("/").lower()


def under_roots(path: str, roots: list[str]) -> bool:
    pn = _norm(path)
    return any(pn == _norm(r) or pn.startswith(_norm(r) + "/") for r in roots)


def bridged() -> bool:
    try:
        from sidecar_bridge.client import client_from_env

        return client_from_env() is not None
    except Exception:
        return False


def _remap_workspace(request):
    ws = getattr(request, "workspace", None)
    wd = str(getattr(ws, "working_dir", "") or "")
    roots = customer_roots()
    if roots and not under_roots(wd, roots):
        logger.info("working_dir %r is outside the customer roots; using %r", wd, roots[0])
        request = request.model_copy(update={"workspace": ws.model_copy(update={"working_dir": roots[0]})})
    return request


def internal_llm_base(base: str | None) -> str | None:
    import os

    pub = (os.environ.get("NOVA_LLM_PUBLIC_BASE") or "").rstrip("/")
    internal = (os.environ.get("NOVA_LLM_INTERNAL_BASE") or "").rstrip("/")
    if base and pub and internal and (base.rstrip("/") == pub or base.startswith(pub + "/")):
        return internal + base[len(pub):]   # LiteLLM serves both /v1/... and /...
    return base


def _install() -> None:
    from openhands.agent_server import conversation_service as cs

    original_start = cs.ConversationService.start_conversation
    if not getattr(original_start, "_nova_wrapped", False):
        @functools.wraps(original_start)
        async def start_conversation(self, request, *args, **kwargs):
            return await original_start(self, _remap_workspace(request), *args, **kwargs)

        start_conversation._nova_wrapped = True
        cs.ConversationService.start_conversation = start_conversation

    original_worktree = cs._create_conversation_worktree
    if not getattr(original_worktree, "_nova_wrapped", False):
        @functools.wraps(original_worktree)
        def create_worktree(workspace, conversation_id, *args, **kwargs):
            wd = str(getattr(workspace, "working_dir", "") or "")
            if _WIN_PATH.match(wd) or bridged():
                return None  # the repo is on the customer's PC; a server-side worktree is meaningless
            try:
                return original_worktree(workspace, conversation_id, *args, **kwargs)
            except Exception:
                logger.warning("worktree creation failed; using the original workspace", exc_info=True)
                return None

        create_worktree._nova_wrapped = True
        cs._create_conversation_worktree = create_worktree
    import pathlib

    original_mkdir = pathlib.Path.mkdir
    if not getattr(original_mkdir, "_nova_wrapped", False):
        @functools.wraps(original_mkdir)
        def mkdir(self, *args, **kwargs):
            try:
                return original_mkdir(self, *args, **kwargs)
            except OSError:
                # only a customer-PC path, only while that customer's workspace is bridged
                p = str(self)
                if bridged() and (_WIN_PATH.match(p) or under_roots(p, customer_roots())):
                    logger.info("not creating customer path %s on the server (bridged workspace)", p)
                    return None
                raise

        mkdir._nova_wrapped = True
        pathlib.Path.mkdir = mkdir

    # Server-side reads under a customer path (project skills / agents / plugins discovery) must not
    # crash when the same path exists on the server but is not readable (e.g. a customer's /root):
    # for a bridged customer path a PermissionError means "not here".
    def _guard(name, empty):
        orig = getattr(pathlib.Path, name)
        if getattr(orig, "_nova_wrapped", False):
            return

        @functools.wraps(orig)
        def wrapped(self, *args, **kwargs):
            try:
                res = orig(self, *args, **kwargs)
                return list(res) if name in ("iterdir", "glob", "rglob") else res
            except PermissionError:
                p = str(self)
                if bridged() and (_WIN_PATH.match(p) or under_roots(p, customer_roots())):
                    return empty()
                raise

        wrapped._nova_wrapped = True
        setattr(pathlib.Path, name, wrapped)

    for _name, _empty in (("exists", lambda: False), ("is_dir", lambda: False), ("is_file", lambda: False),
                          ("iterdir", lambda: iter(())), ("glob", lambda: iter(())), ("rglob", lambda: iter(()))):
        _guard(_name, _empty)

    import importlib
    import os as _os
    import types

    def customer_dir(p) -> bool:
        p = str(p)
        return bridged() and (bool(_WIN_PATH.match(p)) or under_roots(p, customer_roots()))

    class _PathShim(types.ModuleType):
        def __getattr__(self, name):
            return getattr(_os.path, name)

        @staticmethod
        def isdir(p):
            return _os.path.isdir(p) or customer_dir(p)

    class _OsShim(types.ModuleType):
        path = _PathShim("os.path")

        def __getattr__(self, name):
            return getattr(_os, name)

    from openhands.agent_server import event_service as _es

    orig_git = _es.EventService.__dict__.get("_ensure_workspace_is_git_repo")
    if isinstance(orig_git, staticmethod) and not getattr(orig_git.__func__, "_nova_wrapped", False):
        inner = orig_git.__func__

        @functools.wraps(inner)
        def ensure_git(working_dir, *args, **kwargs):
            if customer_dir(working_dir):
                return None
            return inner(working_dir, *args, **kwargs)

        ensure_git._nova_wrapped = True
        _es.EventService._ensure_workspace_is_git_repo = staticmethod(ensure_git)

    for mod_name in ("openhands.tools.terminal.definition", "openhands.tools.glob.definition",
                     "openhands.tools.grep.definition"):
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        if not isinstance(getattr(mod, "os", None), _OsShim):
            mod.os = _OsShim("os")

    from openhands.sdk.llm import llm as llm_mod

    effort_map = {}
    for item in (_os.environ.get("NOVA_REASONING_EFFORT_MAP") or "").split(","):
        model, _, rule = item.strip().partition(":")
        frm, _, to = rule.partition("=")
        if model and frm and to:
            effort_map[(model.strip(), frm.strip())] = to.strip()

    def _ours(llm) -> bool:
        base = str(getattr(llm, "base_url", "") or "")
        return bool(base) and internal_llm_base(base) != base

    def _rewrite_effort(llm, kwargs):
        if not effort_map or not _ours(llm):
            return kwargs
        served = str(llm.model).split("/", 1)[-1]
        for key in ("reasoning_effort",):
            if key in kwargs and (served, kwargs[key]) in effort_map:
                kwargs[key] = effort_map[(served, kwargs[key])]
        r = kwargs.get("reasoning")
        if isinstance(r, dict) and (served, r.get("effort")) in effort_map:
            kwargs["reasoning"] = {**r, "effort": effort_map[(served, r["effort"])]}
        return kwargs

    for name in ("select_chat_options", "select_responses_options"):
        orig = getattr(llm_mod, name)
        if getattr(orig, "_nova_wrapped", False):
            continue

        def make(orig):
            @functools.wraps(orig)
            def wrapped(llm, *args, **kwargs):
                return _rewrite_effort(llm, orig(llm, *args, **kwargs))
            wrapped._nova_wrapped = True
            return wrapped

        setattr(llm_mod, name, make(orig))

    original_kwargs = llm_mod.litellm_call_kwargs
    if not getattr(original_kwargs, "_nova_wrapped", False):
        @functools.wraps(original_kwargs)
        def call_kwargs(model, base_url, *args, **kwargs):
            return original_kwargs(model, internal_llm_base(base_url), *args, **kwargs)

        call_kwargs._nova_wrapped = True
        llm_mod.litellm_call_kwargs = call_kwargs
    logger.info("nova_server_patches installed (customer roots + worktree guard + internal LLM base)")


_install()
