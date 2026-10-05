// Wayland: the installed client under a real Wayland compositor (weston, headless backend) with
// --ozone-platform=wayland. Windows created hidden are never mapped there and never fire
// ready-to-show (customer report 2026-10-05: splash forever). Expect: sign-in window shows, main
// window shows after sign-in, and the app logs which path revealed it.
// NOVA_EXPECT=stuck checks the opposite on an old build: main window never revealed, splash stays
// (reproduces the reported deadlock in this harness - weston headless never emits ready-to-show for it).
// NOVAAGENT_TEST_IGNORE_READY_TO_SHOW=1 forces the fallback path on any compositor.
// Run through e2e/run-wayland.sh.
import { test, expect, _electron as electron } from '@playwright/test'
import path from 'node:path'

const BIN = process.env.NOVA_DESKTOP_BIN || '/opt/NovaAgent/novaagent-client'
const SHOTS = process.env.NOVA_SHOTS || '.'
const EXPECT_STUCK = process.env.NOVA_EXPECT === 'stuck'

const windows = (app) => app.evaluate(({ BrowserWindow }) =>
  BrowserWindow.getAllWindows().map(w => ({ url: w.webContents.getURL(), visible: w.isVisible() })))

async function waitFor(app, pred, ms) {
  const end = Date.now() + ms
  while (Date.now() < end) {
    const ws = await windows(app)
    if (pred(ws)) return ws
    await new Promise(r => setTimeout(r, 500))
  }
  return null
}

test('wayland: windows are revealed (sign-in, then main)', async () => {
  test.setTimeout(300_000)
  const env = { ...process.env }
  for (const k of ['HTTPS_PROXY', 'HTTP_PROXY', 'https_proxy', 'http_proxy', 'DISPLAY']) delete env[k]
  const app = await electron.launch({ executablePath: BIN, args: ['--ozone-platform=wayland'], env })
  let stdout = ''
  app.process().stdout.on('data', d => { stdout += d })
  try {
    const signIn = await waitFor(app, ws => ws.some(w => w.url.includes('login-prompt.html') && w.visible), 30_000)
    expect(signIn, 'sign-in window visible on Wayland').toBeTruthy()
    const login = app.windows().find(w => w.url().includes('login-prompt.html'))
    await login.locator('#username').fill(process.env.E2E_USER)
    await login.locator('#password').fill(process.env.E2E_PASS)
    await login.locator('#save').click()

    if (EXPECT_STUCK) {   // old build: main window never revealed, splash stays (the reported deadlock)
      const shown = await waitFor(app, ws => ws.some(w => w.url.startsWith('https://nova.isigpu.local') && w.visible), 60_000)
      const ws = await windows(app)
      console.log('[old build] windows after 60 s:', JSON.stringify(ws).replace(/t=[0-9a-f]+/g, 't=***'))
      expect(shown, 'old build: main window should never be revealed on Wayland').toBeNull()
      expect(ws.some(w => w.url.includes('loading.html') && w.visible), 'old build: splash still up').toBe(true)
      return
    }
    const main = await waitFor(app, ws => ws.some(w => w.url.startsWith('https://nova.isigpu.local') && w.visible), 180_000)
    console.log('windows:', JSON.stringify(main).replace(/t=[0-9a-f]+/g, 't=***'))
    expect(main, 'main window visible on Wayland').toBeTruthy()
    expect((await windows(app)).some(w => w.url.includes('loading.html')), 'splash closed').toBe(false)
    const m = stdout.match(/main window revealed via ([^\n]+)/)
    console.log('reveal path:', m && m[1])
    expect(m, 'app logs how the main window was revealed').toBeTruthy()
    if (process.env.NOVAAGENT_TEST_IGNORE_READY_TO_SHOW === '1') expect(m[1]).toContain('did-finish-load fallback')
    const win = app.windows().find(w => w.url().startsWith('https://nova.isigpu.local'))
    await win.screenshot({ path: path.join(SHOTS, 'nova-wayland-main.png') }).catch(() => {})
  } finally {
    await app.close()
  }
})
