import { test, expect } from '@playwright/test'
import { login } from './login.js'

const N = 'https://nova.isigpu.local'
const api = (page, path, method = 'GET', body, headers = {}) => page.evaluate(async ([p, m, b, h]) => {
  const r = await fetch(p, { method: m, headers: { 'Content-Type': 'application/json', ...h }, body: b ? JSON.stringify(b) : undefined })
  const t = await r.text(); let j = null; try { j = JSON.parse(t) } catch {}
  return { status: r.status, json: j, text: t.slice(0, 300) }
}, [path, method, body, headers])

test('NovaAgent end to end: SSO, own workspace, seeded LLM key, real agent answer', async ({ page }) => {
  await login(page, N, process.env.E2E_USER, process.env.E2E_PASS)
  await expect(page.locator('body[data-agent-server-ui]')).toBeAttached()
  await page.waitForTimeout(4000)
  await page.screenshot({ path: process.env.NOVA_SHOT || 'nova-home.png' })

  const me = await api(page, '/v1/me')
  expect(me.status, me.text).toBe(200)
  const email = me.json.email
  expect(email).toMatch(/@/)

  // forged identity / session headers must not change who we are
  const forged = await api(page, '/v1/me', 'GET', null, { 'kubeflow-userid': 'someone-else@isigpu.local', 'X-Session-API-Key': 'x' })
  expect(forged.json.email).toBe(email)

  const ws = await api(page, '/v1/workspace', 'POST')
  expect(ws.status, ws.text).toBe(200)
  expect(ws.json.state).toBe('ready')

  const st = await api(page, '/api/settings')
  expect(st.status, st.text).toBe(200)
  expect(st.json.agent_settings.llm.model).toMatch(/^litellm_proxy\//)
  expect(st.json.agent_settings.llm.api_key).toBe('**********')

  const enc = await api(page, '/api/settings', 'GET', null, { 'X-Expose-Secrets': 'encrypted' })
  const conv = await api(page, '/api/conversations', 'POST', {
    workspace: { working_dir: 'workspace/project', kind: 'LocalWorkspace' },
    agent_settings: enc.json.agent_settings, secrets_encrypted: true, tags: { source: 'e2e' },
  })
  expect(conv.status, conv.text).toBeLessThan(300)
  const id = conv.json.id
  const sent = await api(page, `/api/conversations/${id}/events`, 'POST',
    { role: 'user', content: [{ type: 'text', text: 'Do not use any tools. Reply with exactly this word and nothing else: NOVAPONG' }], run: true })
  expect(sent.status, sent.text).toBeLessThan(300)
  await expect.poll(async () => {
    const ev = await api(page, `/api/conversations/${id}/events/search?limit=100`)
    return JSON.stringify(ev.json?.items || []).includes('NOVAPONG') && (ev.json.items.some(e => e.kind === 'MessageEvent' && e.source === 'agent'))
  }, { timeout: 180_000, intervals: [2000] }).toBe(true)
  await api(page, `/api/conversations/${id}`, 'DELETE')
})

test('NovaAgent UI: send a message and see the agent reply stream in (WebSocket via nova-control)', async ({ page }) => {
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  await login(page, N, process.env.E2E_USER, process.env.E2E_PASS)
  await expect(page.getByRole('heading', { name: "Let's Start Building!" })).toBeVisible({ timeout: 120_000 })
  const box = page.locator('[contenteditable="true"]').first()
  await box.click()
  await page.keyboard.type('Do not use any tools. Reply with exactly this word and nothing else: NOVAUIPONG')
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/conversations\//, { timeout: 60_000 })
  // the prompt itself contains the word once; the agent's reply is the second occurrence
  await expect.poll(() => page.getByText('NOVAUIPONG', { exact: false }).count(), { timeout: 180_000, intervals: [2000] }).toBeGreaterThanOrEqual(2)
  await expect(page.getByText('Thinking…')).toHaveCount(0, { timeout: 60_000 })
  await page.screenshot({ path: process.env.NOVA_SHOT2 || 'nova-chat.png' })
  const id = page.url().split('/conversations/')[1].split(/[?#/]/)[0]
  await page.evaluate(async c => { await fetch(`/api/conversations/${c}`, { method: 'DELETE' }) }, id)
  expect(errors, errors.join('\n')).toEqual([])
})
