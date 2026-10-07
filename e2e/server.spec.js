// Dorj Agent server edge (nova-control) as the desktop app and its customers meet it: no web product
// (browsers land on /download/), sign-in contract, tenant token isolation, no forged identities,
// the UI bundle with the company LLM preconfigured, /llmapi limited to model endpoints.
import { test, expect } from '@playwright/test'

const N = 'https://nova.isigpu.local'
const post = (page, path, body, headers = {}) => page.evaluate(async ([p, b, h]) => {
  const r = await fetch(p, { method: 'POST', headers: { 'Content-Type': 'application/json', ...h }, body: JSON.stringify(b) })
  return { status: r.status, json: await r.json().catch(() => null) }
}, [path, body, headers])
const get = (page, path, headers = {}) => page.evaluate(async ([p, h]) => {
  const r = await fetch(p, { headers: h, redirect: 'manual' })
  return { status: r.status, text: (await r.text()).slice(0, 400) }
}, [path, headers])

test('server edge: no web product, sign-in, token isolation, preconfigured LLM', async ({ page }) => {
  // a browser without the app's token sees the download page, not an app
  await page.goto(N + '/')
  expect(new URL(page.url()).pathname).toBe('/download/')

  // sign-in contract (Dorj Agent electron/main.mjs loginToWorkspaceServer)
  const bad = await post(page, '/api/auth/login', { username: process.env.E2E_USER, password: 'wrong' })
  expect(bad.status).toBe(401)
  expect(bad.json.ok).toBe(false)
  const ok = await post(page, '/api/auth/login', { username: process.env.E2E_USER, password: process.env.E2E_PASS })
  expect(ok.status).toBe(200)
  const token = ok.json.token
  expect(token.length).toBeGreaterThanOrEqual(32)

  // identity comes only from the tenant token; Dorj/VM-era headers and the VM cookie-by-name do nothing
  expect((await get(page, '/v1/me')).status).toBe(401)
  expect((await get(page, '/v1/me', { 'kubeflow-userid': 'x@isigpu.local', 'X-Forwarded-Email': 'x@isigpu.local' })).status).toBe(401)
  await page.context().addCookies([{ name: 'ns_t', value: ok.json.customer, url: N }])
  expect((await get(page, '/v1/me')).status).toBe(401)
  const me = await get(page, '/v1/me', { 'X-Session-API-Key': token })
  expect(me.status).toBe(200)
  expect(JSON.parse(me.text).user).toBe(ok.json.customer)

  // the UI bundle as the app loads it (?t=): session key + company LLM injected, no setup screens
  await page.goto(`${N}/?t=${token}`)
  const boot = await page.evaluate(() => ({
    key: window.__AGENT_CANVAS_SESSION_API_KEY__, llm: window.__NOVAAGENT_COMPANY_LLM_URL__,
    session: JSON.parse(localStorage.getItem('novaagent-company-llm-session') || '{}'),
  }))
  expect(boot.key).toBe(token)
  expect(boot.llm).toBe(N + '/llmapi')
  expect(boot.session.access_token).toMatch(/^sk-/)
  await expect(page.locator('[contenteditable="true"]').first()).toBeVisible({ timeout: 120_000 })

  // company LLM: model list with the user's own key; gateway admin endpoints are not reachable
  const auth = { Authorization: 'Bearer ' + boot.session.access_token }
  const models = await page.evaluate(async (h) => (await (await fetch('/llmapi/v1/models', { headers: h })).json()).data.map(m => m.id), auth)
  expect(models).toEqual(expect.arrayContaining(['qwen3', 'glm']))
  expect((await get(page, '/llmapi/key/list', auth)).status).toBe(404)
  expect((await get(page, '/internal/customers', { Authorization: 'Bearer x' })).status).toBe(404)
})
