// NovaAgent desktop — a thin, SSO-native shell around https://nova.isigpu.local.
//
// The agent, its workspace and the LLM keys all live server-side (nova-control + the user's own
// agent-server pod); this app only hosts the web UI in a hardened window. Sign-in is the normal
// Keycloak/Dex SSO flow inside the window (cookies kept in a persistent partition), so there are
// no tokens or passwords in this process and nothing tenant-specific is baked into the package.
// Upstream's thin client (customer token in ?t=, local sidecars) is deliberately not used.
'use strict'

const { app, BrowserWindow, Menu, dialog, ipcMain, session, shell } = require('electron')
const { X509Certificate } = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')

const DEFAULT_SERVER = 'https://nova.isigpu.local'
const PARTITION = 'persist:nova'
// Only the NovaAgent server and its identity provider (Keycloak realm "nova") load inside the window;
// every other link opens in the system browser.
const DEFAULT_AUTH_HOST = 'identity.isigpu.local'

const CONFIG_PATH = () => path.join(app.getPath('userData'), 'config.json')
const fa = () => (app.getLocale() || '').startsWith('fa')
const t = (en, f) => (fa() ? f : en)

function readConfig () {
  try { return JSON.parse(fs.readFileSync(CONFIG_PATH(), 'utf8')) } catch { return {} }
}

function writeConfig (cfg) {
  fs.mkdirSync(path.dirname(CONFIG_PATH()), { recursive: true })
  fs.writeFileSync(CONFIG_PATH(), JSON.stringify(cfg, null, 2) + '\n')
}

function normalizeServer (raw) {
  const u = new URL(String(raw || '').trim())
  if (u.protocol !== 'https:') throw new Error('server must be https://')
  return u.origin
}

function serverUrl () {
  for (const c of [process.env.NOVA_SERVER, readConfig().server]) {
    try { if (c) return normalizeServer(c) } catch {}
  }
  return DEFAULT_SERVER
}

function authHost () {
  return String(process.env.NOVA_AUTH_HOST || readConfig().authHost || DEFAULT_AUTH_HOST).trim().toLowerCase()
}

const isTrustedHost = (host) => host === new URL(serverUrl()).hostname || host === authHost()

function isInternal (url) {
  try {
    const u = new URL(url)
    return u.protocol === 'https:' && isTrustedHost(u.hostname)
  } catch { return false }
}

// The cluster's private root CA ships with the app, so users need not import it into the OS store.
// It is only ever accepted for the server and auth hosts, with a full signature/host/validity check.
const ROOT_CA = (() => {
  try { return new X509Certificate(fs.readFileSync(path.join(__dirname, 'isigpu-root-ca.crt'))) } catch { return null }
})()

function issuedByBundledRoot (hostname, pem) {
  if (!ROOT_CA || !isTrustedHost(hostname)) return false
  try {
    const leaf = new X509Certificate(pem)
    const now = Date.now()
    return leaf.checkIssued(ROOT_CA) && leaf.verify(ROOT_CA.publicKey) && !!leaf.checkHost(hostname) &&
      Date.parse(leaf.validFrom) <= now && now <= Date.parse(leaf.validTo)
  } catch { return false }
}

function hardenSession (ses) {
  ses.setCertificateVerifyProc((req, cb) => {
    if (req.errorCode === 0) return cb(0)            // already trusted by the OS store
    cb(issuedByBundledRoot(req.hostname, req.certificate.data) ? 0 : -2)
  })
  ses.setPermissionRequestHandler((wc, permission, cb) => {
    cb(isInternal(wc.getURL()) && ['clipboard-sanitized-write', 'clipboard-read', 'notifications', 'fullscreen'].includes(permission))
  })
}

let win = null

function loadServer () {
  win.loadURL(serverUrl() + '/').catch(() => {})
}

function showOffline (detail) {
  win.loadFile(path.join(__dirname, 'offline.html'), {
    query: { server: serverUrl(), detail: String(detail || ''), lang: fa() ? 'fa' : 'en' }
  })
}

function createWindow () {
  win = new BrowserWindow({
    width: 1360,
    height: 880,
    minWidth: 900,
    minHeight: 600,
    title: 'NovaAgent',
    backgroundColor: '#0b0e14',
    icon: path.join(__dirname, 'icon.png'),
    show: false,
    webPreferences: {
      partition: PARTITION,
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      spellcheck: false
    }
  })
  win.once('ready-to-show', () => win.show())

  const wc = win.webContents
  wc.setWindowOpenHandler(({ url }) => {
    if (isInternal(url)) { wc.loadURL(url); return { action: 'deny' } }
    if (/^https?:/.test(url)) shell.openExternal(url)
    return { action: 'deny' }
  })
  wc.on('will-navigate', (e, url) => {
    if (url.startsWith('file:') || isInternal(url)) return
    e.preventDefault()
    if (/^https?:/.test(url)) shell.openExternal(url)
  })
  wc.on('did-fail-load', (_e, code, desc, url, isMainFrame) => {
    // -3 = ABORTED (a redirect replaced the load); not an error
    if (isMainFrame && code !== -3 && !url.startsWith('file:')) showOffline(`${desc} (${code})`)
  })
  wc.on('render-process-gone', (_e, d) => showOffline(`renderer ${d.reason}`))
  loadServer()
}

function buildMenu () {
  const sep = { type: 'separator' }
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    ...(process.platform === 'darwin' ? [{ role: 'appMenu' }] : []),
    {
      label: 'NovaAgent',
      submenu: [
        { label: t('Home', 'Safheh-ye asli'), accelerator: 'CmdOrCtrl+Shift+H', click: loadServer },
        { label: t('Server address…', 'Address-e server…'), click: () => showOffline('') },
        { label: t('Sign out', 'Khorooj az hesab'), click: signOut },
        sep,
        { role: 'quit', label: t('Quit', 'Khorooj') }
      ]
    },
    { role: 'editMenu', label: t('Edit', 'Virayesh') },
    {
      label: t('View', 'Namayesh'),
      submenu: [
        { role: 'reload', label: t('Reload', 'Bargozari-e mojadad') },
        { label: t('Back', 'Bazgasht'), accelerator: 'Alt+Left', click: () => win.webContents.navigationHistory.goBack() },
        sep,
        { role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' },
        sep,
        { role: 'togglefullscreen' },
        { role: 'toggleDevTools', label: t('Developer tools', 'Abzar-e tozeh-dahandeh') }
      ]
    },
    {
      label: t('Help', 'Rahnama'),
      submenu: [
        { label: t('Download page / CLI', 'Safheh-ye download / CLI'), click: () => shell.openExternal(serverUrl() + '/download/') },
        { label: t('About', 'Darbareh'), click: () => dialog.showMessageBox(win, { type: 'info', title: 'NovaAgent', message: `NovaAgent ${app.getVersion()}`, detail: serverUrl() }) }
      ]
    }
  ]))
}

async function signOut () {
  // end the SSO session server-side too (same route the web UI's logout uses), then drop cookies
  try { await win.loadURL(serverUrl() + '/oauth2/sign_out') } catch {}
  await session.fromPartition(PARTITION).clearStorageData()
  loadServer()
}

ipcMain.handle('nova:get-server', () => serverUrl())
ipcMain.handle('nova:set-server', (_e, raw) => {
  const server = normalizeServer(raw)
  writeConfig({ ...readConfig(), server })
  loadServer()
  return server
})
ipcMain.handle('nova:retry', () => loadServer())

if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', () => { if (win) { if (win.isMinimized()) win.restore(); win.focus() } })
  app.whenReady().then(() => {
    hardenSession(session.fromPartition(PARTITION))
    buildMenu()
    createWindow()
    app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow() })
  })
  app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit() })
}
