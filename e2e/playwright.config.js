// Dorj Agent live e2e against https://nova.isigpu.local (system Chrome, internal CA ignored).
import { defineConfig } from '@playwright/test'
export default defineConfig({
  testDir: '.',
  timeout: 300_000,
  workers: 1,
  use: {
    channel: 'chrome', headless: true, ignoreHTTPSErrors: true,
    launchOptions: { args: ['--host-resolver-rules=MAP nova.isigpu.local 172.16.50.202, MAP platform.isigpu.local 172.16.50.202'] },
  },
})
