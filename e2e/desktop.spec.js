// NovaAgent desktop client, end to end as a customer uses it: the installed .deb (NovaAgent's own thin
// client + overlay) starts, the sign-in window authenticates against realm nova, the app opens the
// server UI, starts its Local Tools Sidecar + reverse tunnel, and the agent creates a file on *this*
// machine's disk. Runs inside an Ubuntu container under Xvfb (see e2e/run-desktop.sh).
// Needs: E2E_USER/E2E_PASS, NOVA_DESKTOP_BIN (default /opt/NovaAgent/novaagent-client).
import { test, expect, _electron as electron } from '@playwright/test'
import { existsSync, readFileSync, rmSync } from 'node:fs'
import { homedir } from 'node:os'
import path from 'node:path'

const BIN = process.env.NOVA_DESKTOP_BIN || '/opt/NovaAgent/novaagent-client'
const SHOTS = process.env.NOVA_SHOTS || '.'
const PROOF = path.join(homedir(), 'nova_desktop_e2e.txt')

test('desktop client: sign in, server UI, agent works on this PC', async () => {
  test.setTimeout(420_000)
  rmSync(PROOF, { force: true })
  const env = { ...process.env }
  for (const k of ['HTTPS_PROXY', 'HTTP_PROXY', 'https_proxy', 'http_proxy']) delete env[k]
  let main = null
  const app = await electron.launch({ executablePath: BIN, args: ['--no-sandbox', '--disable-gpu'], env })
  try {
    // 1. sign-in window (redesigned login-prompt.html, same IPC contract); a wrong password is refused
    const login = await app.firstWindow()
    await login.locator('#username').waitFor({ timeout: 60_000 })
    expect(await login.locator('html').getAttribute('dir')).toBe('rtl')
    await login.screenshot({ path: path.join(SHOTS, 'nova-desktop-login.png') })
    await login.locator('#username').fill(process.env.E2E_USER)
    await login.locator('#password').fill('wrong-password')
    await login.locator('#save').click()
    await expect(login.locator('#msg')).toContainText('نادرست', { timeout: 30_000 })
    await login.screenshot({ path: path.join(SHOTS, 'nova-desktop-login-error.png') })
    await login.locator('#password').fill(process.env.E2E_PASS)
    await login.locator('#save').click()

    // 2. the server UI in the main window (company LLM pre-configured, no setup screens)
    for (let i = 0; i < 120 && !main; i++) {
      main = app.windows().find(w => w.url().startsWith('https://nova.isigpu.local'))
      if (!main) await new Promise(r => setTimeout(r, 1000))
    }
    expect(main, 'main window on the company server').toBeTruthy()
    await main.waitForLoadState('domcontentloaded')
    await expect(main.locator('[contenteditable="true"]').first()).toBeVisible({ timeout: 180_000 })
    await main.screenshot({ path: path.join(SHOTS, 'nova-desktop-main.png') })
    const me = await main.evaluate(async () => (await fetch('/v1/me')).json())
    expect(me.email).toMatch(/@/)

    // 3. the agent works on this machine: a conversation in this PC's home folder writes a file here
    const result = await main.evaluate(async ({ home }) => {
      const key = window.__AGENT_CANVAS_SESSION_API_KEY__
      const h = { 'Content-Type': 'application/json', 'X-Session-API-Key': key }
      const j = async (r) => { const t = await r.text(); try { return JSON.parse(t) } catch { return { raw: t, status: r.status } } }
      const st = await j(await fetch('/api/settings', { headers: { ...h, 'X-Expose-Secrets': 'encrypted' } }))
      const conv = await j(await fetch('/api/conversations', { method: 'POST', headers: h, body: JSON.stringify({
        workspace: { kind: 'LocalWorkspace', working_dir: home }, agent_settings: st.agent_settings, secrets_encrypted: true }) }))
      if (!conv.id) return { status: 'start-failed', conv }
      const sent = await fetch(`/api/conversations/${conv.id}/events`, { method: 'POST', headers: h, body: JSON.stringify({ role: 'user', run: true,
        content: [{ type: 'text', text: 'Use the file editor to create the file nova_desktop_e2e.txt in the current directory containing exactly: pong. Do not run any other command. Then reply DONE.' }] }) })
      if (!sent.ok) return { id: conv.id, status: 'send-failed', detail: await sent.text() }
      let ran = false
      for (let i = 0; i < 100; i++) {
        const s = (await j(await fetch(`/api/conversations/${conv.id}`, { headers: h }))).execution_status
        if (s === 'running') ran = true
        if (['finished', 'error', 'stuck'].includes(s) || (ran && s === 'idle')) return { id: conv.id, status: s }
        await new Promise(r => setTimeout(r, 3000))
      }
      return { id: conv.id, status: 'timeout' }
    }, { home: homedir() })
    // the agent was told it works on *this* machine (OS/home/shell/runtimes from the sidecar hello)
    const info = await main.evaluate(async (id) => {
      const h = { 'X-Session-API-Key': window.__AGENT_CANVAS_SESSION_API_KEY__ }
      return JSON.stringify(await (await fetch(`/api/conversations/${id}`, { headers: h })).json())
    }, result.id)
    const osName = readFileSync('/etc/os-release', 'utf8').match(/^PRETTY_NAME="?(.+?)"?$/m)[1]
    expect(info).toContain('<USER_MACHINE>')
    expect(info).toContain(osName)
    expect(info).toContain(homedir())
    expect(result.status, JSON.stringify(result)).toBe('finished')
    expect(existsSync(PROOF), `${PROOF} must exist on the customer machine`).toBe(true)
    expect(readFileSync(PROOF, 'utf8').trim()).toBe('pong')
  } finally {
    if (main) {   // what the customer saw last (also on failure)
      await main.screenshot({ path: path.join(SHOTS, 'nova-desktop-last.png') }).catch(() => {})
      const text = await main.evaluate(() => document.body.innerText.slice(0, 2000)).catch(e => String(e))
      console.log('[last main window]', main.url().replace(/t=[^&]+/, 't=***'), '\n', text)
    }
    await app.close()
  }
})
