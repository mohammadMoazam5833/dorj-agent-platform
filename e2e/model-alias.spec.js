import { test, expect } from '@playwright/test'
test('model chip shows alias', async ({ page }) => {
  test.setTimeout(300000)
  await page.goto('https://nova.isigpu.local/download/')
  const tok = await page.evaluate(async ([u, p]) => (await (await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: u, password: p }) })).json()).token, [process.env.E2E_USER, process.env.E2E_PASS])
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('https://nova.isigpu.local/?t=' + tok); await page.waitForTimeout(10000)
  const chip = await page.getByTestId('chat-input-company-llm-model').textContent()
  const any = await page.evaluate(() => document.body.innerText.includes('litellm'))
  console.log('CHIP', chip, 'litellm anywhere:', any)
  await page.screenshot({ path: '/tmp/claude-1000/-workdir/f0c2abe7-7581-44b7-a1d4-f31907ce5b19/scratchpad/nova-chip.png' })
  expect(any).toBe(false)
})
