#!/usr/bin/env python3
"""WebSocket close codes for NovaAgent's scripts/local-tools-sidecar/ws.mjs (shared by the company
gateway - acceptWebSocket - and the customer's reverse-tunnel client - attachMaskedClient).

Upstream always sent an empty CLOSE frame (= 1000, no reason) and dropped the code/reason of a
received CLOSE, so a client could not tell "your session was replaced by another computer" from a
network drop and reconnected forever (customer report 2026-10-05: ~80k reconnects in 2 days with
one account open on two machines). After this patch:

  * sendClose(code?, reason?) sends RFC 6455 close payloads (2-byte code + UTF-8 reason);
  * a received CLOSE is decoded, echoed with the same code, and reported once as
    onClose(code, reason); a socket that dies without a CLOSE frame reports onClose(1006, "").

Applied by nova-platform/gateway/build.sh and nova-platform/desktop/overlay.py. Every edit asserts
its anchor. Usage: ws_close_codes.py <scripts dir>
"""
import pathlib
import sys

p = pathlib.Path(sys.argv[1]) / "local-tools-sidecar" / "ws.mjs"
s = p.read_text(encoding="utf-8")
if "novaClosePayload" in s:
    print("ws.mjs: close codes already patched")
    sys.exit(0)


def patch_fn(src: str, start_marker: str, opts: str, mask: str) -> str:
    """Patch one session implementation (acceptWebSocket uses opts / no mask, the client
    options / mask:true)."""
    a = src.index(start_marker)
    b = src.index("\n}\n", a) + 3
    body = src[a:b]
    edits = [
        ("  let closed = false;\n",
         f"  let closed = false;\n  let novaCloseInfo = null;\n"
         f"  const fireClose = (code, reason) => {{\n"
         f"    if (novaCloseInfo) return;\n"
         f"    novaCloseInfo = {{ code, reason }};\n"
         f"    {opts}.onClose?.(code, reason);\n"
         f"  }};\n"),
        ("  const sendClose = () => {\n",
         "  const sendClose = (code, reason) => {\n"),
        (f"      socket.write(encodeFrame(OP_CLOSE, Buffer.alloc(0){mask}));\n",
         f"      socket.write(encodeFrame(OP_CLOSE, novaClosePayload(code, reason){mask}));\n"),
        (f"    socket.end();\n    {opts}.onClose?.();\n",
         "    socket.end();\n    fireClose(code ?? 1000, reason ?? \"\");\n"),
        ("      if (parsed.opcode === OP_CLOSE) {\n        sendClose();\n        return;\n      }",
         "      if (parsed.opcode === OP_CLOSE) {\n"
         "        const c = novaParseClose(parsed.payload);\n"
         "        sendClose(c.code, c.reason); // echo the peer's code (RFC 6455 5.5.1)\n"
         "        return;\n      }"),
        (f"  socket.on(\"close\", () => {{\n    closed = true;\n    {opts}.onClose?.();\n  }});",
         "  socket.on(\"close\", () => {\n    closed = true;\n    fireClose(1006, \"\");\n  });"),
        (f"  socket.on(\"error\", () => {{\n    closed = true;\n    {opts}.onClose?.();\n  }});",
         "  socket.on(\"error\", () => {\n    closed = true;\n    fireClose(1006, \"\");\n  });"),
    ]
    for old, new in edits:
        if old not in body:
            sys.exit(f"ws_close_codes: anchor not found in {start_marker!r}: {old[:60]!r}")
        body = body.replace(old, new, 1)
    return src[:a] + body + src[b:]


s = patch_fn(s, "export function acceptWebSocket(", "opts", "")
s = patch_fn(s, "function attachMaskedClient(", "options", ", { mask: true }")
s += '''
// ── NovaAgent: RFC 6455 close payloads (nova-platform/patches/ws_close_codes.py) ─────────────────
function novaClosePayload(code, reason) {
  if (!code) return Buffer.alloc(0);
  const text = Buffer.from(String(reason || ""), "utf8").subarray(0, 123);
  const buf = Buffer.alloc(2 + text.length);
  buf.writeUInt16BE(code, 0);
  text.copy(buf, 2);
  return buf;
}

function novaParseClose(payload) {
  if (!payload || payload.length < 2) return { code: 1005, reason: "" };
  return { code: payload.readUInt16BE(0), reason: payload.subarray(2).toString("utf8") };
}
'''
p.write_text(s, encoding="utf-8")
print("ws.mjs: close codes + reasons sent and decoded")
