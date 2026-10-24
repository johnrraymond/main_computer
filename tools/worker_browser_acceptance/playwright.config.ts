import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './test/playwright',
  testMatch: '**/*.spec.ts',
  timeout: 120_000,
  expect: { timeout: 35_000 },
  workers: 1,
  fullyParallel: false,
  retries: 0, // Retrying a key request can create another live authorization.
  reporter: [['list']],
  use: {
    baseURL: process.env.MC_WORKER_TEST_URL || 'http://127.0.0.1:8765/applications/worker',
    headless: false, // MetaMask popup tests require a visible browser.
    actionTimeout: 35_000,
    navigationTimeout: 45_000,
    trace: 'off', // Traces may capture sensitive wallet messages.
    screenshot: 'off', // Save a screenshot of the Worker page only on error below.
    video: 'off',
  },
  outputDir: process.env.MC_WORKER_TEST_OUTPUT_DIR || './test-results',
})
