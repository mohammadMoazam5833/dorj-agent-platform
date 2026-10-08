"""Git for bridged workspaces: Changes tab, per-file accept/revert and checkpoints (2026-10-08).

Cursor phase 1, items 2 and 3. The agent-server's git router runs git on THIS host, but a Dorj Agent
workspace lives on the customer's PC (sidecar_bridge), so the Changes tab saw no repository. While
bridged, these helpers run git on the PC through the sidecar ``exec``/``read_file`` calls:

* ``changes`` / ``diff`` replace the router's ``get_git_changes`` / ``get_git_diff``.
* ``POST /git/accept``  - stage one file (``git add -A -- <file>``): the user keeps the change.
* ``POST /git/revert``  - discard the agent's change to one file (restore from HEAD, or delete a
  file that is new).
* ``POST /git/checkpoints`` / ``GET /git/checkpoints`` / ``POST /git/checkpoints/restore`` -
  snapshots of the working tree kept under ``refs/dorj/checkpoints/*`` (never on the user's
  branches or index). One is taken automatically before each user message. A snapshot holds
  tracked files (``git stash create``) plus the list of untracked files at that moment; restore
  checks out the snapshot's tracked files and removes files that appeared afterwards. Restore
  first takes a "before restore" checkpoint, so it can itself be undone.
"""
from __future__ import annotations

import json
import logging
import ntpath
import posixpath
import re
import time

logger = logging.getLogger(__name__)
_WIN = re.compile(r"^[A-Za-z]:[\\/]")
_REF_PREFIX = "refs/dorj/checkpoints/"
_TIMEOUT_MS = 60_000


class BridgeGitError(RuntimeError):
    pass


def _client():
    from sidecar_bridge.client import client_from_env

    cli = client_from_env()
    if cli is None:
        raise BridgeGitError("workspace is not bridged")
    return cli


def _pm(path: str):
    return ntpath if _WIN.match(path) else posixpath


def git(cwd: str, *args: str, check: bool = True) -> str:
    res = _client().exec(["git", *args], cwd=cwd, timeout_ms=_TIMEOUT_MS)
    code = res.get("exit_code", 1) if isinstance(res, dict) else 1
    if check and code != 0:
        raise BridgeGitError((res.get("stderr") or res.get("stdout") or "git failed").strip()[:500])
    return (res.get("stdout") or "") if isinstance(res, dict) else ""


def git_ok(cwd: str, *args: str) -> bool:
    res = _client().exec(["git", *args], cwd=cwd, timeout_ms=_TIMEOUT_MS)
    return isinstance(res, dict) and res.get("exit_code") == 0


def repo_root(path: str) -> str:
    pm = _pm(path)
    cwd = path
    try:
        _client().list_dir(path)
    except Exception:  # a file path: use its folder
        cwd = pm.dirname(path)
    return git(cwd, "rev-parse", "--show-toplevel").strip().replace("/", "\\" if _WIN.match(path) else "/")


def _rel(root: str, path: str) -> str:
    pm = _pm(root)
    return pm.relpath(path, root).replace("\\", "/")


# --- Changes tab -------------------------------------------------------------------------------
def changes(cwd: str):
    from openhands.sdk.git.models import GitChange, GitChangeStatus

    out = git(cwd, "status", "--porcelain=v1", "-z", "-uall")
    items, parts = [], out.split("\0")
    i = 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        xy, path = entry[:2], entry[3:]
        if "R" in xy:  # rename: the next NUL field is the old path
            i += 1
            status = GitChangeStatus.MOVED
        elif xy == "??" or "A" in xy:
            status = GitChangeStatus.ADDED
        elif "D" in xy:
            status = GitChangeStatus.DELETED
        else:
            status = GitChangeStatus.UPDATED
        items.append(GitChange(status=status, path=path))
    return items


def diff(file_path: str):
    from openhands.sdk.git.models import GitDiff

    root = repo_root(file_path)
    rel = _rel(root, file_path)
    original = git(root, "show", f"HEAD:{rel}", check=False) or None
    try:
        res = _client().read_file(file_path)
        modified = res.get("content") if isinstance(res, dict) else None
    except Exception:
        modified = None  # deleted in the working tree
    return GitDiff(modified=modified, original=original)


# --- per-file accept / revert -----------------------------------------------------------------
def accept(file_path: str) -> dict:
    root = repo_root(file_path)
    rel = _rel(root, file_path)
    git(root, "add", "-A", "--", rel)
    return {"ok": True, "file": rel, "action": "accepted"}


def revert(file_path: str) -> dict:
    root = repo_root(file_path)
    rel = _rel(root, file_path)
    if git_ok(root, "cat-file", "-e", f"HEAD:{rel}"):
        git(root, "checkout", "HEAD", "--", rel)
    else:  # a file the agent created: unstage it and delete it
        git(root, "rm", "-f", "-q", "--cached", "--ignore-unmatch", "--", rel)
        git(root, "clean", "-f", "-q", "--", rel)
    return {"ok": True, "file": rel, "action": "reverted"}


# --- checkpoints -------------------------------------------------------------------------------
def _untracked(root: str) -> list[str]:
    return [p for p in git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0") if p]


def create_checkpoint(path: str, label: str = "") -> dict:
    root = repo_root(path)
    if not git_ok(root, "rev-parse", "--verify", "-q", "HEAD"):
        raise BridgeGitError("repository has no commits yet")
    snap = git(root, "stash", "create").strip() or git(root, "rev-parse", "HEAD").strip()
    tree = git(root, "rev-parse", f"{snap}^{{tree}}").strip()
    meta = {"label": label[:200], "untracked": _untracked(root), "at": int(time.time())}
    commit = git(root, "commit-tree", tree, "-p", "HEAD", "-m", "dorj-checkpoint " + json.dumps(meta, ensure_ascii=False)).strip()
    ref = f"{_REF_PREFIX}{meta['at']}-{commit[:8]}"
    git(root, "update-ref", ref, commit)
    return {"id": ref[len(_REF_PREFIX):], "label": meta["label"], "at": meta["at"]}


def list_checkpoints(path: str, limit: int = 50) -> list[dict]:
    root = repo_root(path)
    out = git(root, "for-each-ref", "--sort=-refname", f"--count={limit}", "--format=%(refname)%00%(contents)%00", _REF_PREFIX)
    items, fields = [], out.split("\0")
    for i in range(0, len(fields) - 1, 2):
        ref, body = fields[i].strip(), fields[i + 1]
        try:
            meta = json.loads(body.split("dorj-checkpoint ", 1)[1])
        except Exception:
            meta = {}
        items.append({"id": ref[len(_REF_PREFIX):], "label": meta.get("label", ""), "at": meta.get("at")})
    return items


def restore_checkpoint(path: str, cp_id: str) -> dict:
    if not re.fullmatch(r"\d{9,11}-[0-9a-f]{8}", cp_id):
        raise BridgeGitError("bad checkpoint id")
    root = repo_root(path)
    ref = _REF_PREFIX + cp_id
    body = git(root, "log", "-1", "--format=%B", ref)
    meta = json.loads(body.split("dorj-checkpoint ", 1)[1])
    undo = create_checkpoint(root, "before restore")
    git(root, "checkout", ref, "--", ".")
    keep = set(meta.get("untracked") or [])
    removed = [p for p in _untracked(root) if p not in keep]
    for i in range(0, len(removed), 50):
        git(root, "clean", "-f", "-q", "--", *removed[i:i + 50])
    return {"ok": True, "restored": cp_id, "removed": removed, "undo": undo["id"]}
