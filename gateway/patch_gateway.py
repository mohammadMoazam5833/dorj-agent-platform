#!/usr/bin/env python3
"""Security fix applied to NovaAgent's scripts/customer-workspace/gateway.mjs at image build time.

Bug (found by e2e/load30.sh, 2026-10-05): the gateway correlates a customer's response with the
waiting agent-server request through one gateway-wide map keyed by the *client-chosen* message id,
and NovaAgent's sidecar_bridge sends every request with id "1". With two customers active at once,
customer A's file contents / command output resolve customer B's request (and requests of one
customer can swap too) - a cross-tenant data leak.

Fix: every forwarded request gets a gateway-unique wire id; the waiter remembers the session it was
sent to and the caller's id; a response is accepted only from that same session and is handed back
with the caller's original id. Report upstream (NovaAgent) as well.

Usage: patch_gateway.py <staged scripts dir>
"""
import pathlib
import sys

p = pathlib.Path(sys.argv[1]) / "customer-workspace" / "gateway.mjs"
s = p.read_text(encoding="utf-8")

EDITS = [
    (  # unique wire ids
        "  const pending = new Map();\n",
        "  const pending = new Map();\n  let wireSeq = 0; // NOVA-FIX: gateway-unique request ids (see nova-platform/gateway/patch_gateway.py)\n",
    ),
    (  # responses only from the session the request went to, returned with the caller's id
        """        const waiter = pending.get(id);
        if (waiter) {
          clearTimeout(waiter.timer);
          pending.delete(id);
          waiter.resolve(parsed);
        }""",
        """        const waiter = pending.get(id);
        if (waiter && waiter.target === entry) {
          clearTimeout(waiter.timer);
          pending.delete(id);
          waiter.resolve({ ...parsed, id: waiter.origId });
        }""",
    ),
    (
        """  function forwardToSession(target, message, timeoutMs) {
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        pending.delete(message.id);""",
        """  function forwardToSession(target, message, timeoutMs) {
    const origId = message.id;
    const wireId = `nova-${++wireSeq}`;
    message = { ...message, id: wireId };
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        pending.delete(wireId);""",
    ),
    (
        """        resolve(
          makeError(
            message.id,
            "timeout",
            `customer workspace timed out after ${timeoutMs}ms`,
          ),
        );
      }, timeoutMs);
      pending.set(message.id, { resolve, timer });""",
        """        resolve(
          makeError(
            origId,
            "timeout",
            `customer workspace timed out after ${timeoutMs}ms`,
          ),
        );
      }, timeoutMs);
      pending.set(wireId, { resolve, timer, target, origId });""",
    ),
    (
        """        clearTimeout(timer);
        pending.delete(message.id);
        resolve(
          makeError(
            message.id,
            "exec_failed",""",
        """        clearTimeout(timer);
        pending.delete(wireId);
        resolve(
          makeError(
            origId,
            "exec_failed",""",
    ),
]
# A newer connection with the same customer token replaces the older one. Tell the older client
# why (4001 session_replaced) so it stops reconnecting instead of kicking the newer one back -
# upstream closed with a bare 1000 and two machines on one account ping-ponged forever.
EDITS.append(("""    if (existing) {
      try {
        existing.sendClose();""", """    if (existing) {
      try {
        console.log(`[gateway] customer '${customer.name}' connected again; closing the previous session (4001 session_replaced)`);
        existing.sendClose(4001, "session_replaced");"""))
EDITS.append(("""          if (entry.timer) clearInterval(entry.timer);
          entry.sendClose();""", """          if (entry.timer) clearInterval(entry.timer);
          entry.sendClose(1001, "gateway_shutdown");"""))
EDITS.append(("      entry.resolve(makeError(id, code, message));",
              "      entry.resolve(makeError(entry.origId ?? id, code, message));"))
for old, new in EDITS:
    if old not in s:
        sys.exit(f"patch_gateway: anchor not found: {old[:70]!r}")
    s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")
print("gateway: per-request wire ids + session-bound responses")
