#!/usr/bin/env python3
"""Apply the Dorj Agent-on-Kubernetes overlay to a staged copy of the Dorj Agent source (electron/).

The Dorj Agent Client (thin Electron client, electron-builder.thin.config.mjs) is built from the
Dorj Agent repo as-is, plus these small, explicit changes. Each edit asserts its anchor so an
upstream change fails the build loudly instead of silently shipping an unpatched client.

  1. Trust the organisation's root CA (bundled as electron/org-root-ca.crt) - for the company
     server windows (Chromium certificate verifier, only the server + identity hosts) and for the
     sidecar / reverse-tunnel child processes (NODE_EXTRA_CA_CERTS).
  2. Sign-in POST and the remote-UI readiness probe go through Electron's net.fetch (Chromium network stack, uses 1.) instead of
     Node's fetch, which does not know the private CA.
  3. A stored token that the server no longer accepts (rotated / disabled) shows the sign-in
     window again instead of opening a dead session.
  4. Application menu with Sign out / Reload / Quit (fa + en).
  5. The redesigned sign-in window (login-prompt.html is replaced; it keeps the same IPC contract).
  6. Windows NSIS installer target for the thin client.
  7. Linux: the launcher wrapper is actually created for the thin client (upstream looked for the
     wrong binary name), and --disable-dev-shm-usage is on every launch path (launcher wrapper, desktop/AppImage
     executableArgs, and in-process). Some VMware Ubuntu guests abort at start with
     "Creating shared memory in /dev/shm failed: No such process (3)" even though /dev/shm is a
     1777 tmpfs (customer report 2026-10-05, 1.6.3 -> 1.6.5); Chromium then keeps shared memory
     under the temp directory instead.

  9. Wayland: the main window (and the splash) are revealed by ready-to-show OR did-finish-load +
     500 ms - a never-shown window is never mapped on Wayland and never fires ready-to-show
     (customer report 2026-10-05: splash forever). Background throttling off for the main window.
 10. Tunnel close codes: the gateway's 4001 session_replaced (account opened on another computer)
     stops the client instead of an endless reconnect loop; the app explains it and offers to take
     the session back. Single app instance; EADDRINUSE on the sidecar port is reported; token
     check has a timeout; no stale VM address in company-llm.json.
 11. Redesigned splash (loading.html): everything in one card, fa/en, same IPC contract.

Usage: overlay.py <staged Dorj Agent dir> <overlay dir (this desktop/ folder)>
"""
import pathlib
import shutil
import subprocess
import sys

stage, overlay = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])


def edit(rel, pairs):
    p = stage / rel
    s = p.read_text(encoding="utf-8")
    for old, new in pairs:
        if old not in s:
            sys.exit(f"overlay: anchor not found in {rel}: {old[:80]!r}")
        s = s.replace(old, new, 1)
    p.write_text(s, encoding="utf-8")


main_edits = [
    (  # 1a + 2: imports
        'import {\n  app,\n  BrowserWindow,\n',
        'import {\n  app,\n  BrowserWindow,\n  Menu,\n  net as electronNet,\n  session,\n',
    ),
    (  # 1b: CA for child processes; must be in the environment before they are spawned
        'const __dirname = dirname(__filename);\n',
        '''const __dirname = dirname(__filename);

// ── Dorj Agent on Kubernetes overlay (nova-platform/desktop/overlay.py) ──────────────────────────
const ORG_CA_PATH = join(__dirname, "org-root-ca.crt");
if (existsSync(ORG_CA_PATH) && !process.env.NODE_EXTRA_CA_CERTS) {
  process.env.NODE_EXTRA_CA_CERTS = ORG_CA_PATH; // sidecar + reverse tunnel children
}
const ORG_TRUSTED_HOSTS = new Set();

/** Accept the bundled org root CA for the company server (+ its identity host) only. */
async function trustOrgCa(remoteUiUrl) {
  if (!existsSync(ORG_CA_PATH)) return;
  const { X509Certificate } = await import("node:crypto");
  const ca = new X509Certificate(readFileSync(ORG_CA_PATH));
  const host = new URL(remoteUiUrl).hostname;
  ORG_TRUSTED_HOSTS.add(host);
  for (const extra of String(process.env.NOVAAGENT_TRUSTED_HOSTS || "").split(",")) {
    if (extra.trim()) ORG_TRUSTED_HOSTS.add(extra.trim());
  }
  const issuedByOrg = (req) => {
    try {
      const leaf = new X509Certificate(req.certificate.data);
      const now = Date.now();
      return leaf.checkIssued(ca) && leaf.verify(ca.publicKey) && Boolean(leaf.checkHost(req.hostname)) &&
        Date.parse(leaf.validFrom) <= now && now <= Date.parse(leaf.validTo);
    } catch {
      return false;
    }
  };
  session.defaultSession.setCertificateVerifyProc((req, cb) => {
    if (req.errorCode === 0) return cb(0);
    if (!ORG_TRUSTED_HOSTS.has(req.hostname)) return cb(-2);
    cb(issuedByOrg(req) ? 0 : -2);
  });
  // The side panel's browser (<webview partition="persist:novapreview">, canvas open_url): intranet
  // sites signed by the org CA open like in the company browser; public sites keep Chromium's check.
  session.fromPartition("persist:novapreview").setCertificateVerifyProc((req, cb) => {
    if (req.errorCode === 0) return cb(0);
    cb(issuedByOrg(req) ? 0 : -2);
  });
}

/** Is the stored workspace token still accepted by the server? (network errors: assume yes) */
async function workspaceTokenStillValid(remoteUiUrl, token) {
  try {
    const res = await electronNet.fetch(new URL("/v1/me", remoteUiUrl).toString(), {
      headers: { "X-Session-API-Key": token },
      signal: AbortSignal.timeout(8000), // a hung /v1/me must not block the whole boot
    });
    return res.status !== 401 && res.status !== 403;
  } catch {
    return true;
  }
}

function forgetWorkspaceToken() {
  try {
    const { userConfigPath } = getThinClientConfigPaths();
    const cfg = JSON.parse(readFileSync(userConfigPath, "utf8"));
    delete cfg.workspaceToken;
    delete cfg.workspaceTokenEncrypted;
    writeFileSync(userConfigPath, `${JSON.stringify(cfg, null, 2)}\\n`, "utf8");
  } catch {
    // nothing stored
  }
}

// Two app instances raced for the sidecar port (18765) - one instance per user session; a second
// launch just brings the first window forward.
if (!app.requestSingleInstanceLock()) {
  app.exit(0);
}
app.on("second-instance", () => {
  const w = [mainWin, loadingWin].find((x) => x && !x.isDestroyed());
  if (w) {
    if (w.isMinimized()) w.restore();
    w.show();
    w.focus();
  }
});

let novaElsewhereDialogOpen = false;
/** The gateway closed our tunnel with a policy code (4001 session_replaced: this account was opened
 *  on another computer). Say so instead of reconnecting forever; offer to take the session back. */
async function showAccountInUseElsewhere(remoteUiUrl) {
  if (novaElsewhereDialogOpen) return;
  novaElsewhereDialogOpen = true;
  try {
    const parent = [mainWin, loadingWin].find((x) => x && !x.isDestroyed()) || undefined;
    const { response } = await dialog.showMessageBox(parent, {
      type: "warning",
      title: "Dorj Agent",
      message: "این حساب در رایانه‌ی دیگری باز است.\\nThis Dorj Agent account is open on another computer.",
      detail:
        "تا وقتی آن‌جا باز است، دستیار به فایل‌های این رایانه دسترسی ندارد. با «استفاده در این رایانه» اتصال به این‌جا منتقل می‌شود.\\n\\n" +
        "While it is open there, the assistant cannot work on this computer's files. «Use it here» moves the connection to this computer.",
      buttons: ["استفاده در این رایانه / Use it here", "بستن / Close"],
      defaultId: 0,
      cancelId: 1,
      noLink: true,
    });
    if (response === 0) startCustomerWorkspaceReverseClient(remoteUiUrl);
  } finally {
    novaElsewhereDialogOpen = false;
  }
}

function showSidecarPortInUse() {
  const parent = [mainWin, loadingWin].find((x) => x && !x.isDestroyed()) || undefined;
  dialog.showMessageBox(parent, {
    type: "error",
    title: "Dorj Agent",
    message: "پورت محلی Dorj Agent (18765) در اختیار برنامه‌ی دیگری است.\\nDorj Agent's local port (18765) is in use by another program.",
    detail: "برنامه‌ی دیگر را ببندید و Dorj Agent را دوباره باز کنید.\\nClose the other program and restart Dorj Agent.",
    buttons: ["OK"],
    noLink: true,
  });
}

function installNovaMenu() {
  const fa = (app.getLocale() || "").startsWith("fa");
  const t = (en, f) => (fa ? f : en);
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    ...(process.platform === "darwin" ? [{ role: "appMenu" }] : []),
    {
      label: "Dorj Agent",
      submenu: [
        {
          label: t("Sign out", "\\u062e\\u0631\\u0648\\u062c \\u0627\\u0632 \\u062d\\u0633\\u0627\\u0628"),
          click: () => { forgetWorkspaceToken(); app.relaunch(); app.exit(0); },
        },
        { type: "separator" },
        { role: "quit", label: t("Quit", "\\u0628\\u0633\\u062a\\u0646") },
      ],
    },
    { role: "editMenu" },
    {
      label: t("View", "\\u0646\\u0645\\u0627\\u06cc\\u0634"),
      submenu: [{ role: "reload" }, { role: "toggleDevTools" }, { type: "separator" },
        { role: "resetZoom" }, { role: "zoomIn" }, { role: "zoomOut" }, { type: "separator" }, { role: "togglefullscreen" }],
    },
  ]));
}
''',
    ),
    (  # 2: sign-in over the Chromium stack
        '    const res = await fetch(u, {\n      method: "POST",',
        '    const res = await electronNet.fetch(u.toString(), {\n      method: "POST",',
    ),
    (  # 2b: the readiness probe of the remote UI also goes over the Chromium stack (private CA)
        '      const res = await fetch(url, { signal: AbortSignal.timeout(2000) });',
        '      const res = await (url.startsWith("https:") ? electronNet.fetch(url, { signal: AbortSignal.timeout(4000) })\n        : fetch(url, { signal: AbortSignal.timeout(2000) }));',
    ),
    (  # 5: larger window for the redesigned sign-in screen
        '    const loginWin = new BrowserWindow({\n      width: 520,\n      height: 480,',
        '    const loginWin = new BrowserWindow({\n      width: 920,\n      height: 600,',
    ),
    (  # 11: the redesigned splash is compact; expanded mode still reveals the startup log
        'const LOADING_WIN_HEIGHT = 360;\nconst LOADING_WIN_EXPANDED_HEIGHT = 560;',
        'const LOADING_WIN_HEIGHT = 300;\nconst LOADING_WIN_EXPANDED_HEIGHT = 540;',
    ),
    (  # match the new Dorj navy pre-paint color
        '    backgroundColor: "#0b0e14",',
        '    backgroundColor: "#0B1B3A",',
    ),
    (  # 1c + 3 + 4: before the thin client decides whether to prompt for sign-in
        '    if (!readWorkspaceTokenFromConfig()) {\n      suppressQuitOnAllWindowsClosed = true;',
        '''    await trustOrgCa(remoteUiUrl);
    installNovaMenu();
    {
      const stored = readWorkspaceTokenFromConfig();
      if (stored && !(await workspaceTokenStillValid(remoteUiUrl, stored))) forgetWorkspaceToken();
    }
    if (!readWorkspaceTokenFromConfig()) {
      suppressQuitOnAllWindowsClosed = true;''',
    ),
]
edit("electron/main.mjs", main_edits)

main_edits_linux = [
    (  # 7: in-process too (renderer/GPU children inherit it)
        '  app.commandLine.appendSwitch("no-sandbox");\n',
        '  app.commandLine.appendSwitch("no-sandbox");\n  app.commandLine.appendSwitch("disable-dev-shm-usage");\n',
    ),
]
edit("electron/main.mjs", main_edits_linux)

edit("electron/main.mjs", [
    (  # Bug 1: on Wayland a never-shown window is never mapped, renders no frame and never emits
       # ready-to-show - the splash stayed forever (customer report 2026-10-05, CDP: DOM complete,
       # 0 rAF frames). ready-to-show stays the primary path; did-finish-load + 500 ms reveals
       # anyway; a "revealed" flag makes it run once.
        """  mainWin.loadURL(uiOrigin);

  mainWin.once("ready-to-show", () => {
    loadingWin?.destroy();
    loadingWin = null;
    mainWin?.show();
    mainWin?.maximize();
  });""",
        """  mainWin.webContents.setBackgroundThrottling(false);
  mainWin.loadURL(uiOrigin);

  let revealed = false;
  const reveal = (via) => {
    if (revealed || !mainWin || mainWin.isDestroyed()) return;
    revealed = true;
    try {
      loadingWin?.destroy();
    } catch {
      // already gone
    }
    loadingWin = null;
    mainWin.show();
    mainWin.maximize();
    console.log(`[desktop] main window revealed via ${via}`);
  };
  // NOVAAGENT_TEST_IGNORE_READY_TO_SHOW=1 (e2e only) drops the primary path so the fallback is
  // exercised on compositors where ready-to-show does fire.
  if (process.env.NOVAAGENT_TEST_IGNORE_READY_TO_SHOW !== "1") {
    mainWin.once("ready-to-show", () => reveal("ready-to-show"));
  }
  mainWin.webContents.once("did-finish-load", () => {
    setTimeout(() => reveal("did-finish-load fallback"), 500);
  });""",
    ),
    (  # ... and the sign-in window (a new user on Wayland would never see it otherwise)
        '    loginWin.loadFile(join(__dirname, "login-prompt.html"));\n    loginWin.once("ready-to-show", () => loginWin.show());',
        '    loginWin.loadFile(join(__dirname, "login-prompt.html"));\n'
        '    loginWin.once("ready-to-show", () => loginWin.show());\n'
        '    loginWin.webContents.once("did-finish-load", () => {\n'
        '      setTimeout(() => { if (!loginWin.isDestroyed() && !loginWin.isVisible()) loginWin.show(); }, 500);\n'
        '    });',
    ),
    (  # ... and the server-address prompt
        '    promptWin.loadFile(join(__dirname, "remote-url-prompt.html"));\n    promptWin.once("ready-to-show", () => promptWin.show());',
        '    promptWin.loadFile(join(__dirname, "remote-url-prompt.html"));\n'
        '    promptWin.once("ready-to-show", () => promptWin.show());\n'
        '    promptWin.webContents.once("did-finish-load", () => {\n'
        '      setTimeout(() => { if (!promptWin.isDestroyed() && !promptWin.isVisible()) promptWin.show(); }, 500);\n'
        '    });',
    ),
    (  # same for the splash itself
        '  loadingWin.loadFile(join(__dirname, "loading.html"));\n  loadingWin.once("ready-to-show", () => loadingWin?.show());',
        '  loadingWin.loadFile(join(__dirname, "loading.html"));\n'
        '  loadingWin.once("ready-to-show", () => loadingWin?.show());\n'
        '  loadingWin.webContents.once("did-finish-load", () => {\n'
        '    setTimeout(() => { if (loadingWin && !loadingWin.isDestroyed() && !loadingWin.isVisible()) loadingWin.show(); }, 500);\n'
        '  });',
    ),
    (  # Bug 2: the tunnel client exits 3 after a policy close (4xxx) - tell the user why
        """    console.log(
      `[desktop] customer workspace client exited (code=${code}, signal=${signal})`,
    );""",
        """    console.log(
      `[desktop] customer workspace client exited (code=${code}, signal=${signal})`,
    );
    if (code === 3) showAccountInUseElsewhere(remoteUiUrl);""",
    ),
    (  # hardening 2: sidecar exits 4 on EADDRINUSE
        """    console.log(
      `[desktop] local tools sidecar exited (code=${code}, signal=${signal})`,
    );""",
        """    console.log(
      `[desktop] local tools sidecar exited (code=${code}, signal=${signal})`,
    );
    if (code === 4) showSidecarPortInUse();""",
    ),
])

# Bug 2 + hardening 2 in the customer-side scripts
subprocess.run([sys.executable, str(overlay.resolve().parent / "patches" / "ws_close_codes.py"), str(stage / "scripts")], check=True)
edit("scripts/customer-workspace/client.mjs", [
    (
        """    try {
      session = await connectWebSocket(options.gatewayWsUrl, {""",
        """    let mine = null; // this connection's session (a late close of an older one must not stop us)
    try {
      session = await connectWebSocket(options.gatewayWsUrl, {""",
    ),
    (
        """        onClose: (info) => {
          stopPinging();
          session = null;
          // 4001/session_replaced means the same account was opened elsewhere.
          // Retrying immediately turns that deliberate eviction into reconnect
          // churn, with each old client fighting the new session.
          if (info?.code === 4001) {
            closed = true;
            log(
              "[customer-workspace-client] session replaced on the gateway; stopped reconnecting",
            );
            return;
          }
          if (!closed && reconnect) scheduleReconnect();
        },
      });""",
        """        onClose: (info) => {
          const code = info?.code;
          const reason = info?.reason || "";
          log(`[customer-workspace-client] closed: code=${code || 1006} reason=${reason || "-"}`);
          if (session && mine && session !== mine) return; // stale socket of a replaced connection
          stopPinging();
          session = null;
          if (code >= 4000 && code < 5000) {
            // policy close from the server (4001 session_replaced = account opened on another
            // computer): reconnecting would just kick the other session - stop and report
            closed = true;
            log(`[customer-workspace-client] stopped: code=${code} reason=${reason || "-"} (not reconnecting)`);
            options.onPolicyClose?.(code, reason);
            return;
          }
          if (!closed && reconnect) scheduleReconnect();
        },
      });
      mine = session;""",
    ),
])
edit("scripts/customer-workspace-client.mjs", [
    (
        """    token,
    reconnect: true,
  });""",
        """    token,
    reconnect: true,
    // the desktop app (electron/main.mjs) shows "account open on another computer" on exit code 3
    onPolicyClose: () => process.exit(3),
  });""",
    ),
    ("company UI origin, e.g. http://172.16.40.188:8000", "company UI origin, e.g. https://nova.example.com"),
])
edit("scripts/customer-workspace/urls.mjs", [("e.g. http://172.16.40.188:8000", "e.g. https://nova.example.com")])
edit("scripts/local-tools-sidecar/server.mjs", [
    (
        "    listen(cb) {\n      server.listen(options.port, options.host, cb);",
        "    listen(cb) {\n"
        "      server.once(\"error\", (err) => {\n"
        "        if (err && err.code === \"EADDRINUSE\") {\n"
        "          console.error(`[local-tools-sidecar] port ${options.host}:${options.port} is already in use (another Dorj Agent or another program)`);\n"
        "          process.exit(4);\n"
        "        }\n"
        "        throw err;\n"
        "      });\n"
        "      server.listen(options.port, options.host, cb);",
    ),
])
# hardening 3: no stale VM address in the shipped company-llm.json (hybrid-only setting)
(stage / "electron/company-llm.json").write_text('{\n  "companyLlmUrl": null\n}\n', encoding="utf-8")
edit("electron-builder.config.mjs", [
    (  # 7a: the thin client's binary is "novaagent-client" (extraMetadata name), which upstream's
       # candidate list misses - without this the .deb ships NO launcher wrapper at all and
       # `novaagent-client` from a terminal starts with none of the Linux flags
        '  const candidates = [\n    join(out, productFilename),',
        '  const candidates = [\n    join(out, context.packager.executableName),\n    join(out, productFilename),',
    ),
    (  # 7b: /usr/bin/novaagent-client is a symlink (update-alternatives); resolve it, or the
       # wrapper looks for /usr/bin/novaagent-client.bin and the app never starts from a terminal
        'DIR="$(cd "$(dirname "$0")" && pwd)"',
        'DIR="$(dirname "$(readlink -f "$0")")"',
    ),
    (  # 7: the launcher wrapper every Linux entry point goes through (/usr/bin, .desktop, AppImage)
        '  --disable-software-rasterizer \\\\\n  "$@"',
        '  --disable-software-rasterizer \\\\\n  --disable-dev-shm-usage \\\\\n  "$@"',
    ),
])

# 8: the customer's machine, as the agent must see it. The sidecar's hello also reports OS, home,
#    the shell its terminal commands run in and the runtimes installed there; the agent-server
#    (nova_server_patches) puts that into the agent's system prompt, so it works with *this*
#    machine's paths, shell and tools - not the Linux server it is hosted on.
edit("scripts/local-tools-sidecar/handlers.mjs", [
    (
        '          roots: ctx.roots,\n          host: ctx.host,\n          port: ctx.port,\n        });',
        '          roots: ctx.roots,\n          host: ctx.host,\n          port: ctx.port,\n          system: describeSystem(),\n        });',
    ),
])
with open(stage / "scripts/local-tools-sidecar/handlers.mjs", "a", encoding="utf-8") as f:
    f.write(r'''

// ── Dorj Agent overlay: describe the customer's machine for the agent (cached) ──────────────────
import * as novaOs from "node:os";
import { execFileSync as novaExecFileSync } from "node:child_process";   // readFileSync/existsSync: imported above

let novaSystemCache = null;

function novaVersionOf(cmd, args) {
  try {
    const out = novaExecFileSync(cmd, args, { encoding: "utf8", timeout: 4000, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
    return String(out).trim().split(/\r?\n/)[0].slice(0, 80) || null;
  } catch (err) {
    const msg = err && (err.stderr || err.stdout);   // java/python2 print versions on stderr
    return msg ? String(msg).trim().split(/\r?\n/)[0].slice(0, 80) : null;
  }
}

function novaOsName() {
  if (process.platform === "linux") {
    try {
      const rel = readFileSync("/etc/os-release", "utf8");
      const m = /^PRETTY_NAME="?(.+?)"?$/m.exec(rel);
      if (m) return m[1];
    } catch { /* ignore */ }
  }
  return (typeof novaOs.version === "function" ? novaOs.version() : "") || novaOs.type();
}

export function describeSystem() {
  if (novaSystemCache) return novaSystemCache;
  const win = process.platform === "win32";
  const shell = win
    ? (WIN_GIT_BASH_CANDIDATES.find((c) => { try { return c && existsSync(c); } catch { return false; } }) || "powershell.exe")
    : (process.env.SHELL || "/bin/bash");
  const probes = {
    python: win ? ["python", ["--version"]] : ["python3", ["--version"]],
    node: ["node", ["--version"]],
    npm: [win ? "npm.cmd" : "npm", ["--version"]],
    git: ["git", ["--version"]],
    java: ["java", ["-version"]],
    go: ["go", ["version"]],
    dotnet: [win ? "dotnet.exe" : "dotnet", ["--version"]],
    docker: ["docker", ["--version"]],
  };
  const runtimes = {};
  for (const [name, [cmd, args]] of Object.entries(probes)) {
    const v = novaVersionOf(cmd, args);
    if (v) runtimes[name] = v;
  }
  let user = "";
  try { user = novaOs.userInfo().username; } catch { /* ignore */ }
  novaSystemCache = {
    platform: process.platform,              // win32 | linux | darwin
    os: novaOsName(),
    release: novaOs.release(),
    arch: process.arch,
    hostname: novaOs.hostname(),
    user,
    home: novaOs.homedir(),
    shell,
    path_separator: win ? "\\" : "/",
    runtimes,
  };
  return novaSystemCache;
}
''')

# Brand assets are copied from the repository source of truth. These anchors are asserted so an
# upstream electron-builder rename cannot silently ship the old NovaAgent icon assets.
thin_cfg = (stage / "electron-builder.thin.config.mjs").read_text(encoding="utf-8")
for anchor in ('    icon: "icon.ico",', '    icon: "icons",', '        Icon: "dorj-agent",'):
    if anchor not in thin_cfg:
        sys.exit(f"overlay: icon anchor not found in electron-builder.thin.config.mjs: {anchor!r}")

brand_dir = overlay / "brand"
brand_target = stage / "electron/brand"
brand_target.mkdir(parents=True, exist_ok=True)
for f in ("dorj-d.png", "dorj-wordmark.png", "dorj-agent-favicon.png"):
    if not (brand_dir / f).is_file():
        sys.exit(f"overlay: brand asset missing: {f}")
    shutil.copy(brand_dir / f, brand_target / f)
build_resources = stage / "electron/build-resources"
build_resources.mkdir(parents=True, exist_ok=True)
shutil.copy(brand_dir / "desktop/dorj-agent.png", build_resources / "icon.png")
shutil.copy(brand_dir / "desktop/dorj-agent.ico", build_resources / "icon.ico")
shutil.rmtree(build_resources / "icons", ignore_errors=True)  # prevent legacy icon sizes persisting
shutil.copytree(brand_dir / "linux", build_resources / "icons")
(build_resources / "novaagent.png").unlink(missing_ok=True)

shutil.copy(overlay / "login-prompt.html", stage / "electron/login-prompt.html")
shutil.copy(overlay / "loading.html", stage / "electron/loading.html")
shutil.copy(overlay / "org-root-ca.crt", stage / "electron/org-root-ca.crt")
for f in ("vazirmatn-400.woff2", "vazirmatn-600.woff2"):
    shutil.copy(overlay / f, stage / "electron" / f)

edit("electron-builder.thin.config.mjs", [
    ('    "login-prompt.html",\n', '    "login-prompt.html",\n    "org-root-ca.crt",\n    "vazirmatn-400.woff2",\n    "vazirmatn-600.woff2",\n    "brand/dorj-d.png",\n    "brand/dorj-wordmark.png",\n    "brand/dorj-agent-favicon.png",\n'),
])
thin = (stage / "electron-builder.thin.config.mjs").read_text(encoding="utf-8")
n = thin.count('      "--disable-gpu",\n    ],')
if n != 2:
    sys.exit(f"overlay: expected 2 Linux executableArgs blocks in the thin config, found {n}")
(stage / "electron-builder.thin.config.mjs").write_text(
    thin.replace('      "--disable-gpu",\n    ],', '      "--disable-gpu",\n      "--disable-dev-shm-usage",\n    ],'), encoding="utf-8")
print("overlay applied")
