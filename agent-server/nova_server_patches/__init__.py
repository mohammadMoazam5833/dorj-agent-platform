"""Dorj Agent server-side behaviour for agent-servers whose tools run on the customer's PC.

Loaded with ``agent-server --import-modules sidecar_bridge,nova_server_patches``. Re-implements, as
runtime wrappers around the stock 1.38.0 package, what Dorj Agent's
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
4. The Dorj Agent UI's company-managed LLM profile points at the public ``https://<host>/llmapi``
   (what the customer's app can reach). Inside the cluster the same calls go straight to the
   internal LiteLLM (NOVA_LLM_PUBLIC_BASE -> NOVA_LLM_INTERNAL_BASE), same per-user key.
7. Identity: the agent is Dorj Agent, not "OpenHands agent". Dorj Agent's own identity file
   (scripts/deploy/SOUL.md, baked at /opt/nova/SOUL.md - ~/.openhands is the user's PVC) is the
   SOUL, and the self-documentation block (which pointed at OpenHands docs/products) describes
   Dorj Agent instead.
6. The agent is told which machine it really works on: the customer's sidecar hello (Dorj Agent
   client overlay) reports OS, home, shell and installed runtimes; it is appended to the agent's
   system prompt (<USER_MACHINE>) for every new conversation.
5. Our models do not all accept every reasoning_effort (qwen3 on vLLM rejects "high", the SDK
   default). NOVA_REASONING_EFFORT_MAP="qwen3:high=medium,..." rewrites the effort for calls to our
   LLM gateway only.

The first two are no-ops when no customer workspace is configured (plain agent-server behaviour).
"""
from __future__ import annotations

import functools
import logging
import os
import re
import time

logger = logging.getLogger("nova_server_patches")
_WIN_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_ROOTS_TTL_S = 30.0
_hello_cache: tuple[float, dict] = (0.0, {})


def customer_hello() -> dict:
    """The customer's sidecar hello (roots + the machine's description), cached briefly;
    {} when the customer's app is not connected / no workspace bridge is configured."""
    global _hello_cache
    now = time.monotonic()
    if now - _hello_cache[0] < _ROOTS_TTL_S and _hello_cache[1]:
        return _hello_cache[1]
    hello: dict = {}
    try:
        from sidecar_bridge.client import client_from_env

        cli = client_from_env()
        if cli is not None:
            res = cli.hello()
            hello = res if isinstance(res, dict) else {}
    except Exception:  # customer offline: keep behaviour unchanged, the tools will report it
        logger.debug("customer hello unavailable", exc_info=True)
    _hello_cache = (now, hello)
    return hello


def customer_roots() -> list[str]:
    raw = customer_hello().get("roots")
    return [str(r).rstrip("/\\") for r in raw if str(r).strip()] if isinstance(raw, list) else []


def customer_system_prompt() -> str:
    """Tell the agent which machine it is really working on (the customer's, through the bridge)."""
    h = customer_hello()
    sysinfo = h.get("system") if isinstance(h.get("system"), dict) else {}
    roots = customer_roots()
    if not sysinfo and not roots:
        return ""
    plat = {"win32": "Windows", "darwin": "macOS", "linux": "Linux"}.get(sysinfo.get("platform", ""), sysinfo.get("platform", "unknown"))
    shell = str(sysinfo.get("shell") or "")
    shell_kind = ("PowerShell" if "powershell" in shell.lower() else "Git Bash" if "bash" in shell.lower() and plat == "Windows"
                  else shell or "the default shell")
    runtimes = sysinfo.get("runtimes") if isinstance(sysinfo.get("runtimes"), dict) else {}
    gpus = sysinfo.get("gpus") if isinstance(sysinfo.get("gpus"), list) else []
    lines = [
        "<USER_MACHINE>",
        "All your file and terminal tools run on the user's own computer (through the Dorj Agent app), "
        "not on a server. Work with this machine's OS, paths, shell and installed tools:",
        f"- OS: {plat} - {sysinfo.get('os') or ''} (release {sysinfo.get('release') or '?'}, {sysinfo.get('arch') or '?'})",
        f"- User: {sysinfo.get('user') or '?'} on host {sysinfo.get('hostname') or '?'}; home: {sysinfo.get('home') or '?'}",
        f"- Terminal commands run in: {shell_kind} ({shell})" if shell else "",
        "- Folders you may use: " + (", ".join(roots) if roots else "(none reported)"),
        "- Installed runtimes: " + (", ".join(f"{k} {v}" for k, v in runtimes.items()) if runtimes
                                    else "none detected - ask before installing anything"),
    ]
    if gpus:
        lines.append("- GPUs: " + ", ".join(str(gpu) for gpu in gpus if str(gpu).strip()))
    if plat == "Windows":
        lines.append("- Use Windows paths (C:\\...) and commands valid in that shell; there is no Linux userland unless Git Bash/WSL is listed.")
    lines.append("Never install system-wide software or change system settings on this machine without the user's explicit consent.")
    lines.append("</USER_MACHINE>")
    return "\n".join(l for l in lines if l)


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


def _with_machine_context(request):
    """Append the customer's machine description to the agent's system prompt."""
    text = customer_system_prompt()
    agent = getattr(request, "agent", None)
    if not text or agent is None or not hasattr(agent, "agent_context"):
        return request
    from openhands.sdk.context.agent_context import AgentContext

    ctx = agent.agent_context or AgentContext()
    cur = ctx.system_message_suffix or ""
    if "<USER_MACHINE>" in cur:
        return request
    ctx = ctx.model_copy(update={"system_message_suffix": (cur + "\n\n" if cur else "") + text})
    return request.model_copy(update={"agent": agent.model_copy(update={"agent_context": ctx})})


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


NOVA_SOUL = ("You are Dorj Agent, an intelligent AI assistant for coding and system tasks. "
             "Your English name is exactly \"Dorj Agent\" and your formal Persian name is exactly "
             "\"دستیار دُرج\". Never replace your name with another product, framework, or internal name.")
NOVA_SELF_DOC = """<SELF_DOCUMENTATION>
When the user asks who you are, what you can do, or how to use this product: you are Dorj Agent
(«دستیار دُرج» in Persian). Use "Dorj Agent" for English replies and «دستیار دُرج» for Persian
replies. Answer from what you know about Dorj Agent and do not refer to any other product, vendor,
framework or website, and never mention internal component names. Dorj Agent:
- runs in the Dorj Agent desktop app (Windows, Linux) and works directly on the user's own computer:
  their folders, files, terminal and installed tools (see <USER_MACHINE> when present);
- plans, writes and edits code across many files, runs builds and tests and fixes until they pass,
  uses git (commits, branches, review-ready changes), and keeps a task list for longer work;
- uses the organisation's own AI models (selectable in the app), with no keys or setup for the user.
</SELF_DOCUMENTATION>"""


def _install_identity() -> None:
    import os as _os

    from openhands.sdk.agent import base as agent_base
    from openhands.sdk.context.prompts.sections import static as prompt_static

    soul_path = _os.environ.get("NOVA_SOUL_PATH", "/opt/nova/SOUL.md")
    if _os.path.isfile(soul_path):
        agent_base._SOUL_PATH = soul_path            # read by _load_soul_md() on every prompt build
    agent_base._DEFAULT_SOUL = NOVA_SOUL
    prompt_static.SoulSection._DEFAULT_SOUL = NOVA_SOUL
    prompt_static.SelfDocumentationSection.body = NOVA_SELF_DOC


def _install_vision_support() -> None:
    """Tell OpenHands that nova-control makes both served models image-capable.

    The /llmapi proxy upscales images for qwen3 and uses qwen3 to extract text for
    glm, which receives that text instead of image bytes (T26).  This list
    contains the served model names, not raw backend capability.
    """
    import os as _os

    from openhands.sdk.llm import llm as llm_mod

    vision_models = {
        item.strip().lower()
        for item in (_os.environ.get("NOVA_VISION_MODELS") or
                     "qwen3,glm").split(",")
        if item.strip()
    }
    original = llm_mod.LLM.vision_is_active
    if getattr(original, "_nova_wrapped", False):
        return

    @functools.wraps(original)
    def vision_is_active(self):
        if self.disable_vision:
            return False
        served = str(self.model).rsplit("/", 1)[-1].strip().lower()
        return served in vision_models or original(self)

    vision_is_active._nova_wrapped = True
    llm_mod.LLM.vision_is_active = vision_is_active
    logger.info(
        "nova_server_patches: local vision models recognized: %s",
        ", ".join(sorted(vision_models)),
    )


def _install() -> None:
    from openhands.agent_server import conversation_service as cs

    original_start = cs.ConversationService.start_conversation
    if not getattr(original_start, "_nova_wrapped", False):
        @functools.wraps(original_start)
        async def start_conversation(self, request, *args, **kwargs):
            return await original_start(self, _with_machine_context(_remap_workspace(request)), *args, **kwargs)

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
                _pc_workspace_started(str(working_dir))
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

    _install_identity()

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


def _install_memory() -> None:
    """Persistent memory lives on the customer's PC, so read it there (2026-10-08).

    The SDK's ``load_memory`` reads ``~/.openhands/memory/MEMORY.md`` (user tier) and
    ``<workspace>/.openhands/memory/MEMORY.md`` (project tier) with ``Path.read_text`` on this
    server, but the agent writes both with its file/terminal tools, which run on the customer's
    PC through sidecar_bridge - so memory was written there and never found here. While the
    workspace is bridged, read the index through the bridge instead: the server home maps to the
    customer's home from the sidecar hello, customer paths are read as they are. Unbridged
    workspaces keep the stock behaviour.
    """
    from pathlib import Path

    from openhands.sdk.context import memory as mem

    original = mem._read_index
    if getattr(original, "_nova_wrapped", False):
        return

    def customer_path(path: Path) -> str | None:
        p = str(path)
        server_home = str(Path.home())
        if p == server_home or p.startswith(server_home + "/"):
            home = str((customer_hello().get("system") or {}).get("home") or "")
            if not home:
                return None
            rel = p[len(server_home):].lstrip("/")
            sep = "\\" if _WIN_PATH.match(home) else "/"
            return home.rstrip("/\\") + sep + rel.replace("/", sep)
        if _WIN_PATH.match(p):  # Path(r"C:\proj") / ".openhands/..." mixes separators on Linux
            return p.replace("/", "\\")
        if under_roots(p, customer_roots()):
            return p
        return None

    @functools.wraps(original)
    def read_index(path):
        if not bridged():
            return original(path)
        target = customer_path(Path(path))
        if target is None:
            return original(path)
        try:
            from sidecar_bridge.client import client_from_env

            res = client_from_env().read_file(target)
        except Exception as exc:  # missing file, customer offline: no memory, never a failure
            logger.debug("memory index %s not readable through the bridge: %s", target, exc)
            return None
        text = (res.get("content") if isinstance(res, dict) else res) or ""
        return str(text).strip() or None

    read_index._nova_wrapped = True
    mem._read_index = read_index
    logger.info("nova_server_patches: persistent memory read from the customer PC through the bridge")


# --- Destructive-command gate on the customer's PC (2026-10-08) ---------------------------------
# The SDK pauses only when the model itself rates an action risky, and "Always allow" turns
# confirmation off entirely. For a tool that runs on employees' PCs IT wants a hard floor:
# destructive commands ALWAYS ask the user, even after "Always allow". This only ADDS
# confirmations; every other action keeps the SDK's own risk-based behaviour.
# Extra patterns: NOVA_CMD_DENY (";;"-separated regexes).
_DENY = [
    r"\brm\s+(-\w*[rR]\w*\s+-\w*f|-\w*f\w*\s+-\w*[rR]|-\w*[rR]\w*f|-\w*f\w*[rR]|--recursive|--force)",
    r"\bsudo\b", r"\bmkfs(\.\w+)?\b", r"\bdd\s+if=", r"\bshred\b",
    r"\b(shutdown|reboot|poweroff|halt)\b", r"\bchmod\s+-R\s+0?777\b",
    r"\bgit\s+push\b.*(\s-f\b|--force)", r"\bgit\s+reset\s+--hard\b", r"\bgit\s+clean\s+-\w*f",
    r"\bgit\s+branch\s+-D\b", r"\bgit\s+filter-(branch|repo)\b",
    r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba|z)?sh\b", r"(?i)\b(iwr|irm|Invoke-WebRequest|Invoke-RestMethod)\b.*\|\s*iex\b",
    r"(?i)\bdel\s+(/\w+\s+)*/[sq]\b", r"(?i)\b(rd|rmdir)\s+(/\w+\s+)*/s\b", r"(?i)\bformat\s+[a-z]:",
    r"(?i)\bRemove-Item\b.*-Recurse", r"(?i)\b(Stop|Restart)-Computer\b", r"(?i)\bdiskpart\b",
    r"(?i)\breg\s+delete\b", r"(?i)\b(DROP|TRUNCATE)\s+(DATABASE|TABLE|SCHEMA)\b",
    r"\bnpm\s+publish\b", r"\bkubectl\s+delete\b", r"\bdocker\s+(system\s+prune|rm\s+-f)\b",
    r">\s*/dev/sd[a-z]",
]


def is_destructive(command: str) -> bool:
    extra = [p for p in os.environ.get("NOVA_CMD_DENY", "").split(";;") if p.strip()]
    return any(re.search(p, command) for p in _DENY + extra)


def _install_command_gate() -> None:
    from openhands.sdk.agent import agent as agent_mod
    from openhands.sdk.conversation.state import ConversationExecutionStatus

    Agent = agent_mod.Agent
    original = Agent._requires_user_confirmation
    if getattr(original, "_nova_wrapped", False):
        return

    @functools.wraps(original)
    def requires_confirmation(self, state, action_events):
        risky = [c for c in (getattr(getattr(ev, "action", None), "command", None) for ev in action_events or [])
                 if isinstance(c, str) and is_destructive(c)]
        if risky:
            logger.info("command gate: user confirmation required for %r", risky)
            state.execution_status = ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
            return True
        return original(self, state, action_events)

    requires_confirmation._nova_wrapped = True
    Agent._requires_user_confirmation = requires_confirmation
    logger.info("nova_server_patches: destructive-command gate installed")


def _install_workspace_git() -> None:
    """Changes tab, accept/revert and checkpoints on the customer's PC (see workspace_git.py)."""
    import asyncio

    from fastapi import Body, HTTPException, Query

    from openhands.agent_server import event_service as _es
    from openhands.agent_server import git_router as gr

    from . import workspace_git as wg

    def on_pc(path) -> bool:
        p = str(path)
        return bridged() and (bool(_WIN_PATH.match(p)) or under_roots(p, customer_roots()))

    for name, bridged_fn in (("get_git_changes", wg.changes), ("get_git_diff", wg.diff),
                             ("get_git_commits", wg.commits), ("get_commit_changes", wg.commit_changes),
                             ("get_commit_file_diff", wg.commit_file_diff)):
        original = getattr(gr, name)
        if getattr(original, "_nova_wrapped", False):
            continue

        def make(original, bridged_fn):
            @functools.wraps(original)
            def wrapped(path, *args, **kwargs):
                if not on_pc(path):
                    return original(path, *args, **kwargs)
                try:
                    return bridged_fn(str(path), *args, **kwargs)
                except wg.GitNotInstalled as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                except wg.BridgeGitError as exc:
                    from openhands.sdk.git.exceptions import GitRepositoryError
                    raise GitRepositoryError(str(exc)) from exc
            wrapped._nova_wrapped = True
            return wrapped

        setattr(gr, name, make(original, bridged_fn))

    if getattr(gr.git_router, "_nova_routes", False):
        return

    async def call(fn, *args):
        try:
            return await asyncio.get_running_loop().run_in_executor(None, fn, *args)
        except wg.BridgeGitError as exc:  # includes GitNotInstalled (detail starts git_not_installed:)
            raise HTTPException(status_code=400, detail=str(exc))

    @gr.git_router.post("/accept")
    async def git_accept(path: str = Body(..., embed=True)) -> dict:
        """Keep the agent's change to one file (stage it)."""
        return await call(wg.accept, path)

    @gr.git_router.post("/revert")
    async def git_revert(path: str = Body(..., embed=True)) -> dict:
        """Discard the agent's change to one file."""
        return await call(wg.revert, path)

    @gr.git_router.get("/checkpoints")
    async def git_checkpoints(path: str = Query(...)) -> list[dict]:
        return await call(wg.list_checkpoints, path)

    @gr.git_router.post("/checkpoints")
    async def git_checkpoint_create(path: str = Body(...), label: str = Body("")) -> dict:
        return await call(wg.create_checkpoint, path, label)

    @gr.git_router.post("/checkpoints/restore")
    async def git_checkpoint_restore(path: str = Body(...), id: str = Body(...)) -> dict:
        return await call(wg.restore_checkpoint, path, id)

    @gr.git_router.post("/init")
    async def git_init(path: str = Body(..., embed=True)) -> dict:
        """Make a workspace folder on the PC a git repository."""
        if not on_pc(path):
            raise HTTPException(status_code=400, detail="path is not in the connected workspace")
        return await call(wg.init_repo, path)

    gr.git_router._nova_routes = True

    # automatic checkpoint before every user message (best effort, never blocks the message)
    original_send = _es.EventService.send_message
    if not getattr(original_send, "_nova_wrapped", False):
        @functools.wraps(original_send)
        async def send_message(self, message, *args, **kwargs):
            try:
                wd = str(getattr(getattr(self.stored, "workspace", None), "working_dir", "") or "")
                if wd and on_pc(wd):
                    text = " ".join(getattr(c, "text", "") for c in (getattr(message, "content", None) or []))
                    await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(
                        None, wg.create_checkpoint, wd, text.strip()[:120]), timeout=20)
            except Exception as exc:  # not a git repo / customer offline: no checkpoint
                logger.debug("auto checkpoint skipped: %s", exc)
            return await original_send(self, message, *args, **kwargs)

        send_message._nova_wrapped = True
        _es.EventService.send_message = send_message
    logger.info("nova_server_patches: bridged git (changes/diff/accept/revert/checkpoints) installed")


# --- Workspace file upload/download on the customer's PC (2026-10-09, owner bug report) -----------
# agent-server's /api/file/upload|download read/write THIS host: a Windows path ("C:\\...") is not
# absolute on Linux -> 400 "Path must be absolute", and a Linux PC's path silently landed on the pod.
# While bridged, customer paths go through the sidecar: UTF-8 text with the v1 protocol, binary only
# when the client announces the "file_base64" capability (T19); otherwise a clear bilingual 400.
_FILE_MAX = 25 * 1024 * 1024


def _client_caps() -> set:
    caps = customer_hello().get("capabilities")
    return set(caps) if isinstance(caps, list) else set()


def _install_file_bridge() -> None:
    import base64
    import mimetypes

    from fastapi import HTTPException
    from fastapi.responses import Response
    from openhands.agent_server import file_router as fr

    def on_pc(path) -> bool:
        p = str(path)
        return bridged() and (bool(_WIN_PATH.match(p)) or under_roots(p, customer_roots()))

    def cli():
        from sidecar_bridge.client import client_from_env
        c = client_from_env()
        if c is None:
            raise HTTPException(status_code=503, detail="workspace is not connected")
        return c

    orig_upload, orig_download = fr._upload_file, fr._download_file
    if getattr(orig_upload, "_nova_wrapped", False):
        return

    @functools.wraps(orig_upload)
    async def upload(path, file):
        if not on_pc(path):
            return await orig_upload(path, file)
        data = await file.read(_FILE_MAX + 1)
        if len(data) > _FILE_MAX:
            raise HTTPException(status_code=413, detail="file larger than 25 MB / فایل بزرگ‌تر از ۲۵ مگابایت است")
        try:
            text = data.decode("utf-8")
            params = {"path": path, "content": text, "create_parents": True}
        except UnicodeDecodeError:
            if "file_base64" not in _client_caps():
                raise HTTPException(status_code=400, detail=(
                    "Uploading binary files needs the latest Dorj Agent app / "
                    "برای بارگذاری این نوع فایل نسخه‌ی جدید دستیار دُرج را نصب کنید"))
            params = {"path": path, "content": base64.b64encode(data).decode("ascii"),
                      "encoding": "base64", "create_parents": True}
        try:
            cli().request("write_file", params)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"upload failed on your computer: {exc}") from exc
        from openhands.agent_server.models import Success
        return Success()

    @functools.wraps(orig_download)
    async def download(path):
        if not on_pc(path):
            return await orig_download(path)
        try:
            if "file_base64" in _client_caps():
                res = cli().request("read_file", {"path": path, "encoding": "base64"})
                body = base64.b64decode(res.get("content") or "")
            else:
                res = cli().read_file(path)
                body = str(res.get("content") or "").encode("utf-8")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=404, detail=f"file not readable on your computer: {exc}") from exc
        name = re.split(r"[\\/]", str(path))[-1] or "file"
        mt = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return Response(content=body, media_type=mt, headers={"Content-Disposition": f'attachment; filename="{name}"'})

    upload._nova_wrapped = True
    fr._upload_file, fr._download_file = upload, download
    logger.info("nova_server_patches: workspace file upload/download bridged to the customer PC")


# --- Conversation start on the PC + the file viewer panel (2026-10-09, owner report on Windows) ---
# A conversation without a chosen folder gets "<PC home>/workspace/project/<hex>". EventService.start()
# then mkdir()s it on THIS host (a Windows path is relative on Linux -> junk dir under the pod's cwd)
# and git-inits it here, so the Changes tab never saw a repo. Upstream git-inits the workspace; do the
# same on the PC, only for these app-made folders (never a folder the user picked).
_DEFAULT_PROJECT_DIR = re.compile(r"[\\/]workspace[\\/]project[\\/][0-9a-f]{32}$")


def _pc_workspace_started(working_dir: str) -> None:
    import os as _os
    import threading
    from pathlib import Path as _P

    junk = _P(working_dir)
    if not junk.is_absolute():  # remove the empty tree start() just made on the server
        try:
            _os.removedirs(junk)
        except OSError:
            pass
    if not _DEFAULT_PROJECT_DIR.search(working_dir):
        return
    root = next((r for r in customer_roots() if under_roots(working_dir, [r])), None)
    if root is None:
        return

    def init() -> None:
        from . import workspace_git as wg
        try:
            if not wg.git_ok(root, "-C", working_dir, "rev-parse", "--git-dir"):
                wg.git(root, "init", "-q", working_dir)  # creates the folder on the PC as well
        except Exception as exc:  # git missing / PC offline: the Changes tab explains it
            logger.info("nova_server_patches: git init on the PC skipped for %s: %s", working_dir, exc)

    threading.Thread(target=init, name="nova-pc-git-init", daemon=True).start()


def _install_workspace_files() -> None:
    """GET /api/conversations/{id}/workspace/{file}: the file panel reads from the PC, not the pod."""
    import base64
    import mimetypes

    from fastapi import HTTPException
    from fastapi.responses import Response
    from openhands.agent_server import workspace_router as wr

    if getattr(wr._serve_path, "_nova_wrapped", False):
        return
    orig_resolve, orig_serve = wr._resolve_workspace_dir, wr._serve_path

    class PCDir(str):
        """A workspace folder on the customer's PC (string path, never touched locally)."""

    async def resolve(conversation_id, conversation_service):
        event_service = await conversation_service.get_event_service(conversation_id)
        wd = str(getattr(getattr(getattr(event_service, "stored", None), "workspace", None), "working_dir", "") or "")
        if wd and bridged() and (_WIN_PATH.match(wd) or under_roots(wd, customer_roots())):
            return PCDir(wd.rstrip("\\/"))
        return await orig_resolve(conversation_id, conversation_service)

    def read(path: str) -> bytes:
        from sidecar_bridge.client import client_from_env
        c = client_from_env()
        if c is None:
            raise HTTPException(status_code=503, detail="workspace is not connected")
        if "file_base64" in _client_caps():
            res = c.request("read_file", {"path": path, "encoding": "base64"})
            return base64.b64decode(res.get("content") or "")
        return str(c.read_file(path).get("content") or "").encode("utf-8")

    def serve(workspace_dir, file_path: str):
        if not isinstance(workspace_dir, PCDir):
            return orig_serve(workspace_dir, file_path)
        parts = [p for p in re.split(r"[\\/]+", file_path or "") if p not in ("", ".")]
        if ".." in parts:
            raise HTTPException(status_code=400, detail="Path is outside the workspace")
        sep = "\\" if _WIN_PATH.match(workspace_dir) else "/"
        if _WIN_PATH.match(file_path or ""):  # the agent showed an absolute PC path (canvas_ui_control)
            if not under_roots(file_path, customer_roots()):
                raise HTTPException(status_code=400, detail="Path is outside the shared folders")
            target = file_path[:2] + "\\" + "\\".join(parts[1:])
        else:
            target = sep.join([str(workspace_dir), *parts])
        for candidate in ([target] if parts else []) + [target + sep + "index.html"]:
            try:
                body = read(candidate)
            except HTTPException:
                raise
            except Exception:
                continue
            mt = mimetypes.guess_type(candidate.replace("\\", "/"))[0] or "application/octet-stream"
            if mt.startswith("text/") or mt in ("application/json", "application/javascript"):
                mt += "; charset=utf-8"
            return Response(content=body, media_type=mt)
        raise HTTPException(status_code=404, detail="File not found")

    serve._nova_wrapped = True
    wr._resolve_workspace_dir, wr._serve_path = resolve, serve
    logger.info("nova_server_patches: conversation workspace files served from the customer PC")


# --- Panel commands on the PC (2026-10-09): the Files tab lists the workspace with `find` through
# POST /api/bash/execute_bash_command (and the Terminal tab uses start_bash_command). Those ran a
# subprocess on THIS pod, so with a PC working dir the Files tab stayed empty ("No such file or
# directory: C:\\Users\\..."). Run them through the sidecar like the agent's terminal tool.
def _install_bash_bridge() -> None:
    import asyncio

    from openhands.agent_server import bash_service as bs
    from openhands.agent_server.models import BashOutput

    orig = bs.BashEventService._execute_bash_command
    if getattr(orig, "_nova_wrapped", False):
        return

    @functools.wraps(orig)
    async def execute(self, command):
        cwd = str(command.cwd or "")
        if not (cwd and bridged() and (_WIN_PATH.match(cwd) or under_roots(cwd, customer_roots()))):
            return await orig(self, command)
        try:
            from sidecar_bridge.client import client_from_env

            client = client_from_env()
            if client is None:
                raise RuntimeError("workspace is not connected")
            res = await asyncio.to_thread(client.exec, ["/bin/bash", "-lc", command.command or "true"],  # = the agent terminal (Git Bash on Windows)
                                          cwd=cwd, timeout_ms=max(1, int(command.timeout)) * 1000)
            out = BashOutput(command_id=command.id, order=0,
                             exit_code=-1 if res.get("timed_out") else int(res.get("exit_code") or 0),
                             stdout=res.get("stdout") or None, stderr=res.get("stderr") or None)
        except Exception as exc:  # noqa: BLE001 - reported to the caller like the original
            out = BashOutput(command_id=command.id, order=0, exit_code=-1,
                             stderr=f"Error executing command on your computer: {exc}")
        self._save_event_to_file(out)
        await self._pub_sub(out)

    execute._nova_wrapped = True
    bs.BashEventService._execute_bash_command = execute
    logger.info("nova_server_patches: panel bash commands run on the customer PC")


_install_vision_support()
_install()
_install_memory()
_install_command_gate()
_install_workspace_git()
_install_file_bridge()
_install_workspace_files()
_install_bash_bridge()
