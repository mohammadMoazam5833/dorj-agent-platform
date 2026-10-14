// T25 profile page in the live UI bundle (the bundle the desktop app loads): account menu -> profile,
// subscription tab with the three real agent plans, usage tab fed by the agent usage ledger.
import { test, expect } from '@playwright/test'

const N = 'https://nova.isigpu.local'

test('profile: account menu, real agent plans, usage', async ({ page }) => {
  await page.goto(`${N}/download/`)
  const ok = await page.evaluate(async ([u, p]) => (await fetch('/api/auth/login', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: u, password: p }) })).json(),
  [process.env.E2E_USER, process.env.E2E_PASS])
  await page.goto(`${N}/?t=${ok.token}`)
  await expect(page.locator('[contenteditable="true"]').first()).toBeVisible({ timeout: 120_000 })

  await page.getByTestId('nova-account-menu-button').click()
  await page.getByTestId('nova-profile-link').click()
  await expect(page).toHaveURL(/\/profile/)
  await page.getByTestId('nova-profile-tab-subscription').click()
  for (const id of ['a200', 'a500', 'a1200']) await expect(page.getByTestId(`buy-plan-${id}`)).toBeEnabled()
  await page.screenshot({ path: 'test-results/live-profile-subscription.png', fullPage: true })
  await page.getByTestId('nova-profile-tab-usage').click()
  await expect(page.getByRole('main')).toContainText(/qwen3|glm/i)
  await page.screenshot({ path: 'test-results/live-profile-usage.png', fullPage: true })
})
