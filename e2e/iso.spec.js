// Per-user isolation: two tenants on one PC/browser. A creates a conversation; B (same browser profile, like
// switching account in the desktop app) must not see it - neither via the API nor in the UI sidebar.
import { test, expect } from '@playwright/test'
import fs from 'fs'
const N = 'https://nova.isigpu.local'
const [A, B] = fs.readFileSync(process.env.ISO_TOKENS, 'utf8').trim().split('\n').map(l => l.split(' ')[1])

test('conversations are per user (API + UI)', async ({ page }) => {
  test.setTimeout(600_000)
  await page.goto(N + '/download/')
  const api = (tok, path, opt = {}) => page.evaluate(async ([tok, path, opt]) => {
    const r = await fetch(path, { ...opt, headers: { 'Content-Type': 'application/json', 'X-Session-API-Key': tok, ...(opt.headers || {}) } })
    return { status: r.status, body: await r.text() }
  }, [tok, path, opt])
  // warm both workspaces
  for (const t of [A, B]) { const r = await api(t, '/api/settings'); console.log('settings', r.status) }
  const st = JSON.parse((await api(A, '/api/settings', { headers: { 'X-Expose-Secrets': 'encrypted' } })).body)
  const conv = JSON.parse((await api(A, '/api/conversations', { method: 'POST', body: JSON.stringify({
    workspace: { kind: 'LocalWorkspace', working_dir: 'workspace/project' }, agent_settings: st.agent_settings, secrets_encrypted: true, title: 'ISO-A-SECRET' }) })).body)
  console.log('A created', conv.id)
  const la = JSON.parse((await api(A, '/api/conversations/search?limit=50')).body)
  const lb = JSON.parse((await api(B, '/api/conversations/search?limit=50')).body)
  const ids = (l) => (l.items || []).map(c => c.id)
  console.log('A sees', ids(la).length, 'B sees', ids(lb).length, 'B sees A conv:', ids(lb).includes(conv.id))
  expect(ids(la)).toContain(conv.id)
  expect(ids(lb)).not.toContain(conv.id)
  // UI: open as A, then as B in the SAME browser profile
  await page.goto(N + '/?t=' + A); await page.waitForTimeout(8000)
  await page.screenshot({ path: 'iso-ui-a.png' })
  await page.goto(N + '/?t=' + B); await page.waitForTimeout(8000)
  await page.screenshot({ path: 'iso-ui-b.png' })
  const leaked = await page.evaluate((id) => document.body.innerHTML.includes(id), conv.id)
  const ls = await page.evaluate(() => Object.keys(localStorage))
  console.log('UI as B shows A conversation id:', leaked, 'localStorage keys:', ls.join(','))
  const req = []
  await api(A, `/api/conversations/${conv.id}`, { method: 'DELETE' })
  expect(leaked).toBe(false)
})
