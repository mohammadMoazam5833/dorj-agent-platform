// Dorj Agent desktop client, end to end as a customer uses it: the installed .deb (Dorj Agent thin
// client + overlay) starts, the sign-in window authenticates against realm nova, the app opens the
// server UI, starts its Local Tools Sidecar + reverse tunnel, and the agent creates a file on *this*
// machine's disk. Runs inside an Ubuntu container under Xvfb (see e2e/run-desktop.sh).
// Needs: E2E_USER/E2E_PASS, NOVA_DESKTOP_BIN (default /usr/bin/dorj-agent-client).
import { test, expect, _electron as electron } from '@playwright/test'
import { existsSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import path from 'node:path'

const BIN = process.env.NOVA_DESKTOP_BIN || '/usr/bin/dorj-agent-client'
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

    // 4. T19: workspace upload/download and `git init` reach THIS machine (Windows bug 2026-10-09:
    //    upload ran on the server -> "Path must be absolute"); 3 MB binary needs file_base64
    const dir = path.join(homedir(), 'nova_t19')
    rmSync(dir, { recursive: true, force: true })
    const t19 = await main.evaluate(async ({ dir }) => {
      const h = { 'X-Session-API-Key': window.__AGENT_CANVAS_SESSION_API_KEY__ }
      const bin = new Uint8Array(3 * 1024 * 1024).map((_, i) => (i * 31 + 7) & 255)
      const up = async (name, blob) => {
        const fd = new FormData(); fd.append('file', blob, name)
        const r = await fetch(`/api/file/upload?path=${encodeURIComponent(dir + '/' + name)}`, { method: 'POST', headers: h, body: fd })
        return r.status + ' ' + (await r.text()).slice(0, 200)
      }
      const text = await up('note.txt', new Blob(['سلام دُرج\n']))
      const binary = await up('img.bin', new Blob([bin]))
      const dl = new Uint8Array(await (await fetch(`/api/file/download?path=${encodeURIComponent(dir + '/img.bin')}`, { headers: h })).arrayBuffer())
      const same = dl.length === bin.length && dl.every((b, i) => b === bin[i])
      const init = await fetch('/api/git/init', { method: 'POST', headers: { ...h, 'Content-Type': 'application/json' }, body: JSON.stringify({ path: dir }) })
      return { text, binary, same, init: init.status + ' ' + (await init.text()).slice(0, 200) }
    }, { dir })
    console.log('[T19]', JSON.stringify(t19)); writeFileSync(path.join(SHOTS, 't19.json'), JSON.stringify(t19))
    expect(t19.text).toMatch(/^200/)
    expect(t19.binary).toMatch(/^200/)
    expect(t19.same, 'downloaded bytes equal uploaded bytes').toBe(true)
    expect(t19.init).toMatch(/^200/)
    expect(readFileSync(path.join(dir, 'note.txt'), 'utf8')).toBe('سلام دُرج\n')
    expect(readFileSync(path.join(dir, 'img.bin')).length).toBe(3 * 1024 * 1024)
    expect(existsSync(path.join(dir, '.git')), 'git init ran on this machine').toBe(true)
  } finally {
    if (main) {   // what the customer saw last (also on failure)
      await main.screenshot({ path: path.join(SHOTS, 'nova-desktop-last.png') }).catch(() => {})
      const text = await main.evaluate(() => document.body.innerText.slice(0, 2000)).catch(e => String(e))
      console.log('[last main window]', main.url().replace(/t=[^&]+/, 't=***'), '\n', text)
    }
    await app.close()
  }
})
