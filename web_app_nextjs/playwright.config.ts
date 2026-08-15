import { defineConfig, devices } from "@playwright/test";

/**
 * E2E config for the Create Account form tests.
 *
 * Assumes the Next.js dev server is reachable at BASE_URL (default :3002).
 * By default Playwright will start the dev server for you; set
 * PW_NO_SERVER=1 to reuse an already-running instance.
 */
const BASE_URL = process.env.PW_BASE_URL || "http://localhost:3002";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: false,
  reporter: "list",
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: process.env.PW_NO_SERVER
    ? undefined
    : {
        command: "npm run dev -- --port 3002",
        url: BASE_URL,
        reuseExistingServer: true,
        timeout: 120_000,
      },
});
