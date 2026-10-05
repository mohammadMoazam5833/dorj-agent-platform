#!/usr/bin/env python3
"""Apply the NovaAgent-on-Kubernetes overlay to a staged copy of the NovaAgent source (electron/).

The NovaAgent Client (thin Electron client, electron-builder.thin.config.mjs) is built from the
NovaAgent repo as-is, plus these small, explicit changes. Each edit asserts its anchor so an
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

Usage: overlay.py <staged NovaAgent dir> <overlay dir (this desktop/ folder)>
"""
import pathlib
import shutil
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

// ── NovaAgent on Kubernetes overlay (nova-platform/desktop/overlay.py) ──────────────────────────
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
  session.defaultSession.setCertificateVerifyProc((req, cb) => {
    if (req.errorCode === 0) return cb(0);
    if (!ORG_TRUSTED_HOSTS.has(req.hostname)) return cb(-2);
    try {
      const leaf = new X509Certificate(req.certificate.data);
      const now = Date.now();
      const ok = leaf.checkIssued(ca) && leaf.verify(ca.publicKey) && Boolean(leaf.checkHost(req.hostname)) &&
        Date.parse(leaf.validFrom) <= now && now <= Date.parse(leaf.validTo);
      cb(ok ? 0 : -2);
    } catch {
      cb(-2);
    }
  });
}

/** Is the stored workspace token still accepted by the server? (network errors: assume yes) */
async function workspaceTokenStillValid(remoteUiUrl, token) {
  try {
    const res = await electronNet.fetch(new URL("/v1/me", remoteUiUrl).toString(), {
      headers: { "X-Session-API-Key": token },
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
    writeFileSync(userConfigPath, `${JSON.stringify(cfg, null, 2)}\\n`, "utf8");
  } catch {
    // nothing stored
  }
}

function installNovaMenu() {
  const fa = (app.getLocale() || "").startsWith("fa");
  const t = (en, f) => (fa ? f : en);
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    ...(process.platform === "darwin" ? [{ role: "appMenu" }] : []),
    {
      label: "NovaAgent",
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

// ── NovaAgent overlay: describe the customer's machine for the agent (cached) ──────────────────
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

shutil.copy(overlay / "login-prompt.html", stage / "electron/login-prompt.html")
shutil.copy(overlay / "org-root-ca.crt", stage / "electron/org-root-ca.crt")
for f in ("vazirmatn-400.woff2", "vazirmatn-600.woff2"):
    shutil.copy(overlay / f, stage / "electron" / f)

edit("electron-builder.thin.config.mjs", [
    ('    "login-prompt.html",\n', '    "login-prompt.html",\n    "org-root-ca.crt",\n    "vazirmatn-400.woff2",\n    "vazirmatn-600.woff2",\n'),
    ('  productName: "NovaAgent Client",', '  productName: "NovaAgent",'),
    ('    target: [\n      { target: "nsis", arch: ["x64"] },\n      { target: "zip", arch: ["x64"] },\n    ],',
     '    target: [{ target: "nsis", arch: ["x64"] }],'),
    ('    shortcutName: "NovaAgent Client",', '    shortcutName: "NovaAgent",'),
    ('    artifactName: "NovaAgent-Client-Setup-${version}.${ext}",', '    artifactName: "NovaAgent-Setup-${version}-x64.${ext}",'),
    ('    artifactName: "NovaAgent-Client-${version}-${arch}.${ext}",\n  },\n\n  deb:', '    artifactName: "NovaAgent-${version}-${arch}.${ext}",\n  },\n\n  deb:'),
])
thin = (stage / "electron-builder.thin.config.mjs").read_text(encoding="utf-8")
n = thin.count('      "--disable-gpu",\n    ],')
if n != 2:
    sys.exit(f"overlay: expected 2 Linux executableArgs blocks in the thin config, found {n}")
(stage / "electron-builder.thin.config.mjs").write_text(
    thin.replace('      "--disable-gpu",\n    ],', '      "--disable-gpu",\n      "--disable-dev-shm-usage",\n    ],'), encoding="utf-8")
print("overlay applied")
