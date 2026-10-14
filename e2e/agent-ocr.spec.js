// Live T26 e2e: upscaled qwen3 vision, and qwen3 extraction in front of glm.
// Owner runs this after deployment with E2E_USER=e2e-dorjagent and E2E_PASS.
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { test, expect } from '@playwright/test'

const BASE = 'https://nova.isigpu.local'
const ASSET = fileURLToPath(new URL('../../faranegar/e2e/assets/table-people-fa.png', import.meta.url))
const TRUTH = JSON.parse(readFileSync(new URL('../../faranegar/e2e/assets/table-people-fa.json', import.meta.url), 'utf8'))
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

async function selectModel(page, model) {
  const chip = page.getByTestId('chat-input-company-llm-model')
  await expect(chip).toBeVisible({ timeout: 120_000 })
  if ((await chip.getAttribute('title')) !== model) {
    await chip.click()
    const option = page.getByTestId(`chat-input-company-model-option-${model}`)
    await expect(option).toBeVisible({ timeout: 30_000 })
    await option.click()
  }
  await expect(chip).toHaveAttribute('title', model)
}

async function chatText(page) {
  return page.getByTestId('chat-scroll-container').innerText()
}

async function isBusy(page) {
  return page.locator([
    '[data-testid="chat-message-sending"]',
    '[data-testid="chat-message-stop"]',
    '[data-testid="typing-indicator"]'
  ].join(',')).count()
}

async function waitForTableCells(page, baseline) {
  // The agent can emit progress/status text after the table; require both a
  // no-busy UI and two consecutive identical snapshots before scoring it.
  let stable = ''
  for (let i = 0; i < 300; i++) {
    await page.waitForTimeout(1000)
    if (await isBusy(page)) { stable = ''; continue }
    const text = await chatText(page)
    if (text === stable && text.length > baseline.length) break
    stable = text
  }
  const text = await chatText(page)
  const matched = matchedCells(text)
  console.log(`[T26] matched ${matched}/${CELLS.length} cells`)
  expect(matched, `expected ${REQUIRED_CELLS} of ${CELLS.length} table cells`).toBeGreaterThanOrEqual(REQUIRED_CELLS)
}

test('agent OCR: glm and qwen3 both read the Persian table', async ({ page }) => {
  test.setTimeout(900_000)
  if (!process.env.E2E_USER || !process.env.E2E_PASS) throw new Error('E2E_USER/E2E_PASS are required')

  await signIn(page)
  for (const model of ['glm', 'qwen3']) {
    await expect(page.getByTestId('chat-interface')).toBeVisible({ timeout: 120_000 })
    await selectModel(page, model)

    const box = page.locator('[contenteditable="true"]').first()
    await expect(box).toBeVisible({ timeout: 120_000 })
    await page.getByTestId('upload-image-input').setInputFiles({
      name: 'table-people-fa.png', mimeType: 'image/png', buffer: readFileSync(ASSET)
    })
    await expect(page.getByTestId('image-preview').or(page.locator('img[alt="table-people-fa.png"]')).first())
      .toBeVisible({ timeout: 60_000 })
    const baseline = await chatText(page)
    await box.click()
    await box.fill('تمام متن و جدول این تصویر را دقیق استخراج کن. فقط جدول markdown را برگردان، بدون توضیح.')
    await page.keyboard.press('Enter')
    await waitForTableCells(page, baseline)
    console.log(`[T26] ${model} met the table threshold`)
  }
})
