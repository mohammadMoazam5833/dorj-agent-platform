// NovaAgent desktop app live e2e: launches the real Electron app (desktop/app), signs in through SSO
// inside the app window, and checks the hardening (bundled CA only for *.isigpu.local, external links
// leave the app, no Node in remote pages).
// Needs: E2E_USER/E2E_PASS and a packaged build (desktop: npm run dist:linux) - the real artifact is
// what gets tested (in CI: the installed .deb, NOVA_DESKTOP_BIN=/opt/NovaAgent/novaagent under xvfb-run).
import { test, expect, _electron as electron } from '@playwright/test'
import path from 'node:path'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'

const DESKTOP = path.resolve(__dirname, '../desktop')
const RESOLVE = process.env.NOVA_HOST_RULES || 'MAP nova.isigpu.local 172.16.50.202, MAP platform.isigpu.local 172.16.50.202, MAP identity.isigpu.local 172.16.50.202'
const userData = mkdtempSync(path.join(tmpdir(), 'nova-desktop-e2e-'))
test.afterAll(() => rmSync(userData, { recursive: true, force: true }))

test('desktop app: SSO sign-in in the window, real UI, hardened shell', async () => {
  const env = { ...process.env }
  for (const k of ['HTTPS_PROXY', 'HTTP_PROXY', 'https_proxy', 'http_proxy']) delete env[k]
  const app = await electron.launch({
    executablePath: process.env.NOVA_DESKTOP_BIN || path.join(DESKTOP, 'dist/linux-unpacked/novaagent'),
    args: ['--no-sandbox', '--disable-gpu', `--host-resolver-rules=${RESOLVE}`, `--user-data-dir=${userData}`],
    env
  })
  try {
    const win = await app.firstWindow()
    // OS store on this host does not trust the cluster CA: getting past TLS proves the bundled-CA check
    const pw = win.locator('input[type="password"]:visible').first()
    await pw.waitFor({ timeout: 60_000 })
    expect(new URL(win.url()).hostname).toMatch(/\.isigpu\.local$/)
    const form = win.locator('form', { has: pw })
    await form.locator('input:visible:not([type="password"]):not([type="hidden"]):not([type="checkbox"])').first().fill(process.env.E2E_USER)
    await pw.fill(process.env.E2E_PASS)
    await win.waitForFunction(u => { const h = document.getElementById('username'); return !h || h.type !== 'hidden' || h.value.startsWith(u.split('@')[0] + '@') }, process.env.E2E_USER)
    await pw.press('Enter')
    await win.waitForURL(u => u.hostname === 'nova.isigpu.local' && !/\/(oauth2|dex)\//.test(u.pathname), { timeout: 60_000 })
    await expect(win.locator('body[data-agent-server-ui]')).toBeAttached({ timeout: 30_000 })

    const me = await win.evaluate(async () => (await fetch('/v1/me')).json())
    expect(me.email).toMatch(new RegExp('^' + process.env.E2E_USER.split('@')[0] + '@', 'i'))
    expect(await win.evaluate(() => typeof require === 'undefined' && typeof process === 'undefined' && typeof window.nova === 'undefined')).toBe(true)
    // the real UI renders and a chat round-trips through the agent (WebSocket via nova-control)
    await expect(win.getByRole('heading', { name: "Let's Start Building!" })).toBeVisible({ timeout: 120_000 })
    await win.locator('[contenteditable="true"]').first().click()
    await win.keyboard.type('Do not use any tools. Reply with exactly this word and nothing else: NOVADESKPONG')
    await win.keyboard.press('Enter')
    await expect(win).toHaveURL(/\/conversations\//, { timeout: 60_000 })
    await expect.poll(() => win.getByText('NOVADESKPONG', { exact: false }).count(), { timeout: 180_000, intervals: [2000] }).toBeGreaterThanOrEqual(2)
    await win.screenshot({ path: process.env.NOVA_DESKTOP_SHOT || 'nova-desktop.png' })
    const conv = win.url().split('/conversations/')[1].split(/[?#/]/)[0]
    await win.evaluate(async c => { await fetch(`/api/conversations/${c}`, { method: 'DELETE' }) }, conv)

    // main-process policy checks
    const policy = await app.evaluate(async ({ session, app: a }) => {
      const s = session.fromPartition('persist:nova')
      const cookies = await s.cookies.get({ domain: 'nova.isigpu.local' })
      return { cookies: cookies.length, version: a.getVersion(), name: a.getName() }
    })
    expect(policy.cookies).toBeGreaterThan(0)
    expect(policy.version).toBe(require(path.join(DESKTOP, 'app/package.json')).version)

    // a foreign https origin must not load in the app window (opened externally instead)
    // neither a foreign site nor another isigpu.local product (Dorj) opens inside the app
    await win.evaluate(() => { location.href = 'https://platform.isigpu.local/' })
    await win.waitForTimeout(1500)
    expect(new URL(win.url()).hostname).toBe('nova.isigpu.local')
    await win.evaluate(() => { location.href = 'https://example.com/' })
    await win.waitForTimeout(1500)
    expect(new URL(win.url()).hostname).toBe('nova.isigpu.local')
    expect(win.url()).not.toContain('example.com')
  } finally {
    await app.close()
  }
})
