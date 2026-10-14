// Live T26 e2e: upscaled qwen3 vision, and qwen3 extraction in front of glm.
// Owner runs this after deployment with E2E_USER=e2e-dorjagent and E2E_PASS.
const { readFileSync } = require('node:fs')
const path = require('node:path')
const { test, expect } = require('@playwright/test')

const BASE = 'https://nova.isigpu.local'
const ASSET = path.join(__dirname, '../../faranegar/e2e/assets/table-people-fa.png')
const TRUTH = JSON.parse(readFileSync(path.join(__dirname, '../../faranegar/e2e/assets/table-people-fa.json'), 'utf8'))
const CELLS = [TRUTH.header, ...TRUTH.rows].flat()
// The task set the acceptance bar at 50/56 cells.  The checked-in synthetic table
// has 72 cells, so keep the same pass rate (89.3%) rather than lowering it to 50.
const REQUIRED_CELLS = Math.ceil((CELLS.length * 50) / 56)

function normalize(value) {
  return String(value || '')
    .normalize('NFKC')
    .replace(/\u200c/g, '')
    .replace(/ي/g, 'ی')
    .replace(/ك/g, 'ک')
    .replace(/[^\p{L}\p{N}]/gu, '')
}

function matchedCells(text) {
  const normalized = normalize(text)
  return CELLS.filter(cell => normalized.includes(normalize(cell))).length
}

async function signIn(page) {
  await page.goto(BASE + '/download/')
  const token = await page.evaluate(async ([user, pass]) => {
    const r = await fetch('/api/auth/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: user, password: pass })
    })
    const body = await r.json()
    if (!r.ok || !body.token) throw new Error(`login failed: ${r.status}`)
    return body.token
  }, [process.env.E2E_USER, process.env.E2E_PASS])

  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`${BASE}/?t=${token}`)
}

// The browser UI cannot start a conversation without the desktop client's sidecar, so drive the same
// agent-server API the UI uses (as desktop.spec.js does). The image goes agent-server -> /llmapi -> OCR.
async function askAgent(page, model, dataUrl, prompt) {
  return page.evaluate(async ({ model, dataUrl, prompt }) => {
    const key = window.__AGENT_CANVAS_SESSION_API_KEY__
    const h = { 'Content-Type': 'application/json', 'X-Session-API-Key': key }
    const j = async (r) => { const t = await r.text(); try { return JSON.parse(t) } catch { return { raw: t, status: r.status } } }
    const st = await j(await fetch('/api/settings', { headers: { ...h, 'X-Expose-Secrets': 'encrypted' } }))
    const agent_settings = { ...st.agent_settings, llm: { ...st.agent_settings.llm, model: `litellm_proxy/${model}` } }
    const conv = await j(await fetch('/api/conversations', { method: 'POST', headers: h, body: JSON.stringify({
      workspace: { kind: 'LocalWorkspace', working_dir: '/tmp' }, agent_settings, secrets_encrypted: true }) }))
    if (!conv.id) return { error: 'start-failed ' + JSON.stringify(conv).slice(0, 300) }
    const sent = await fetch(`/api/conversations/${conv.id}/events`, { method: 'POST', headers: h, body: JSON.stringify({
      role: 'user', run: true, content: [{ type: 'text', text: prompt }, { type: 'image', image_urls: [dataUrl] }] }) })
    if (!sent.ok) return { error: 'send-failed ' + (await sent.text()).slice(0, 300) }
    let ran = false, status = 'timeout'
    for (let i = 0; i < 200; i++) {
      const s = (await j(await fetch(`/api/conversations/${conv.id}`, { headers: h }))).execution_status
      if (s === 'running') ran = true
      if (['finished', 'error', 'stuck'].includes(s) || (ran && s === 'idle')) { status = s; break }
      await new Promise(r => setTimeout(r, 3000))
    }
    const ev = await j(await fetch(`/api/conversations/${conv.id}/events/search?limit=100`, { headers: h }))
    const items = ev.items || ev.events || []
    const text = items.filter(e => e.source === 'agent').map(e => JSON.stringify(e.llm_message || e.action || e)).join('\n')
    return { id: conv.id, status, text }
  }, { model, dataUrl, prompt })
}

test('agent OCR: glm and qwen3 both read the Persian table', async ({ page }) => {
  test.setTimeout(900_000)
  if (!process.env.E2E_USER || !process.env.E2E_PASS) throw new Error('E2E_USER/E2E_PASS are required')
  await signIn(page)
  await page.waitForFunction(() => !!window.__AGENT_CANVAS_SESSION_API_KEY__, null, { timeout: 120_000 })
  const dataUrl = 'data:image/png;base64,' + readFileSync(ASSET).toString('base64')
  const prompt = 'تمام متن و جدول این تصویر را دقیق استخراج کن. فقط جدول markdown را برگردان، بدون توضیح. از هیچ ابزاری استفاده نکن.'
  const results = {}
  for (const model of ['glm', 'qwen3']) {
    const r = await askAgent(page, model, dataUrl, prompt)
    expect(r.error, r.error).toBeUndefined()
    // JSON-escaped text: decode \\uXXXX / \\n so cells compare as plain text
    const plain = JSON.parse(JSON.stringify(r.text))
    results[model] = matchedCells(plain)
    console.log(`[T26] ${model} conv=${r.id} status=${r.status} matched ${results[model]}/${CELLS.length} cells`)
  }
  for (const model of ['glm', 'qwen3'])
    expect(results[model], `${model}: expected ${REQUIRED_CELLS}/${CELLS.length} table cells`).toBeGreaterThanOrEqual(REQUIRED_CELLS)
})
