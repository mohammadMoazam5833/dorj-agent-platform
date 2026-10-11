// Needs two synthetic tenants: see the header of iso.spec.js (created/removed with nova_control.tenants like load30.sh).
// Account switch on one PC with a backend entry left in localStorage by the previous account (older client
// versions / an edited backend): B must still only see B's conversations.
import { test, expect } from '@playwright/test'
import fs from 'fs'
const N = 'https://nova.isigpu.local'
const [A, B] = fs.readFileSync(process.env.ISO_TOKENS, 'utf8').trim().split('\n').map(l => l.split(' ')[1])
test('stale backend registry from another account does not leak', async ({ page }) => {
  test.setTimeout(600_000)
  await page.goto(N + '/download/')
  const api = (tok, path, opt = {}) => page.evaluate(async ([tok, path, opt]) => {
    const r = await fetch(path, { ...opt, headers: { 'Content-Type': 'application/json', 'X-Session-API-Key': tok } }); return r.text()
  }, [tok, path, opt])
  const st = JSON.parse(await api(A, '/api/settings'))
  const conv = JSON.parse(await api(A, '/api/conversations', { method: 'POST', body: JSON.stringify({
    workspace: { kind: 'LocalWorkspace', working_dir: 'workspace/project' }, agent_settings: st.agent_settings }) }))
  await page.goto(N + '/?t=' + A); await page.waitForTimeout(6000)
  const stored = await page.evaluate(() => localStorage.getItem('openhands-backends'))
  console.log('stored backends after A:', stored.replace(/"apiKey":"[^"]+"/g, '"apiKey":"***"'))
  // what an older client / an edited entry leaves behind: same key, host written differently
  await page.evaluate(() => {
    const b = JSON.parse(localStorage.getItem('openhands-backends'))
    b[0].host = b[0].host.replace(/\/?$/, '/'); localStorage.setItem('openhands-backends', JSON.stringify(b))
  })
  const seen = []
  // only requests issued by B's page (in-flight polling of A's page during the navigation is A's own, legit)
  page.on('response', r => { const q = r.request(); if (q.url().includes('/api/conversations') && r.status() === 200 && q.frame().url().includes(B)) seen.push(q.headers()['x-session-api-key'] === A ? 'A' : q.headers()['x-session-api-key'] === B ? 'B' : 'other') })
  if (0) page.on('request', r => { if (r.url().includes('/api/conversations')) seen.push(r.headers()['x-session-api-key'] === A ? 'A' : r.headers()['x-session-api-key'] === B ? 'B' : 'other') })
  await page.goto(N + '/?t=' + B); await page.waitForTimeout(10000)
  const leaked = await page.evaluate((id) => document.body.innerHTML.includes(id), conv.id)
  console.log('requests as:', [...new Set(seen)].join(','), 'B UI shows A conversation:', leaked)
  await page.screenshot({ path: 'iso-stale-b.png' })
  await api(A, `/api/conversations/${conv.id}`, { method: 'DELETE' })
  expect(seen).not.toContain('A')
})
