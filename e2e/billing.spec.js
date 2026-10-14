// T25 Dorj Agent billing on the live edge: profile + plans + wallet from chat-api's internal API, /llmapi calls
// made with only the user's LiteLLM key (as agent-servers do) are attributed and land in the usage ledger,
// purchase accepts only known plan ids and keeps one open payment per user.
import { test, expect } from '@playwright/test'

const N = 'https://nova.isigpu.local'
const call = (page, method, path, body, headers = {}) => page.evaluate(async ([m, p, b, h]) => {
  const r = await fetch(p, { method: m, headers: { 'Content-Type': 'application/json', ...h }, body: b == null ? undefined : JSON.stringify(b) })
  const text = await r.text()
  let json = null
  try { json = JSON.parse(text) } catch {}
  return { status: r.status, json, text: text.slice(0, 2000) }
}, [method, path, body, headers])

test('agent billing: profile, plans, usage attribution, purchase guard', async ({ page }) => {
  test.setTimeout(240_000)
  await page.goto(`${N}/download/`)
  const ok = await call(page, 'POST', '/api/auth/login', { username: process.env.E2E_USER, password: process.env.E2E_PASS })
  expect(ok.status).toBe(200)
  const nova = { 'X-Session-API-Key': ok.json.token }

  const me = await call(page, 'GET', '/api/me', null, nova)
  expect(me.status).toBe(200)
  expect(me.json.account.username).toBe(ok.json.customer)
  expect(me.json.wallet.product).toBe('agent')
  expect(me.json.wallet.unavailable).toBeUndefined()

  const plans = await call(page, 'GET', '/api/billing/plans', null, nova)
  expect(plans.status).toBe(200)
  expect(plans.json.plans.map(p => [p.id, p.price_toman])).toEqual([['a200', 200000], ['a500', 500000], ['a1200', 1200000]])
  expect((await call(page, 'GET', '/api/billing/plans')).status).toBe(401)

  const before = await call(page, 'GET', '/api/me/usage', null, nova)
  expect(before.status).toBe(200)
  const seen = (await call(page, 'GET', '/api/billing/history', null, nova)).json.usage.length

  // an agent-server style call: the user's LiteLLM key only, no nova token
  await page.goto(`${N}/?t=${ok.json.token}`)
  const key = await page.evaluate(() => JSON.parse(localStorage.getItem('novaagent-company-llm-session') || '{}').access_token)
  expect(key).toMatch(/^sk-/)
  const llm = { Authorization: 'Bearer ' + key }
  const msg = [{ role: 'user', content: 'Reply with the single word: ok' }]
  const plain = await call(page, 'POST', '/llmapi/v1/chat/completions', { model: 'qwen3', messages: msg, max_tokens: 64 }, llm)
  expect(plain.status, plain.text).toBe(200)
  const streamed = await page.evaluate(async (h) => {
    const r = await fetch('/llmapi/v1/chat/completions', { method: 'POST', headers: { 'Content-Type': 'application/json', ...h },
      body: JSON.stringify({ model: 'qwen3', stream: true, max_tokens: 64, messages: [{ role: 'user', content: 'Say hi' }] }) })
    return { status: r.status, body: await r.text() }
  }, llm)
  expect(streamed.status).toBe(200)
  expect(streamed.body).toContain('data:')
  // a key nobody owns is refused before reaching LiteLLM
  expect((await call(page, 'POST', '/llmapi/v1/chat/completions', { model: 'qwen3', messages: msg }, { Authorization: 'Bearer sk-not-a-real-key' })).status).toBe(401)

  // both completions reach the agent usage ledger (reported asynchronously, retried)
  let hist
  for (let i = 0; i < 30; i++) {
    hist = await call(page, 'GET', '/api/billing/history', null, nova)
    if (hist.json.usage.length >= Math.min(seen + 2, 50)) break
    await page.waitForTimeout(1500)
  }
  expect(hist.json.usage.length).toBeGreaterThanOrEqual(Math.min(seen + 2, 50))
  expect((await call(page, 'GET', '/api/me/usage', null, nova)).json.tokens).toBeGreaterThan(before.json.tokens)
  expect(hist.json.usage.slice(0, 2).every(u => u.model === 'qwen3' && u.tokens_out > 0)).toBe(true)

  // purchase: unknown plans never reach chat-api; a real plan opens the (test) gateway once
  expect((await call(page, 'POST', '/api/billing/purchase', { plan_id: 'p100' }, nova)).status).toBe(400)
  const buy = await call(page, 'POST', '/api/billing/purchase', { plan_id: 'a200', return_url: 'https://evil.example/' }, nova)
  expect(buy.status, buy.text).toBe(200)
  expect(buy.json.reference).toMatch(/^DA/)
  expect(buy.json.redirect_url).not.toContain('evil.example')
  const again = await call(page, 'POST', '/api/billing/purchase', { plan_id: 'a500' }, nova)
  expect(again.status).toBe(409)
  const pay = (await call(page, 'GET', '/api/billing/history', null, nova)).json.payments[0]
  expect([pay.reference, pay.plan, pay.amount_rial, pay.status]).toEqual([buy.json.reference, 'a200', 2_000_000, 'redirected'])
})
