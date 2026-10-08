import { defineConfig, devices } from "@playwright/test";
const e2eApiPort = process.env.RESEARCHFORGE_E2E_API_PORT || "8003";
const e2eWebPort = process.env.RESEARCHFORGE_E2E_WEB_PORT || "3012";
export default defineConfig({
  testDir: "./tests", workers: 1, timeout: 60000,
  expect: { timeout: Number(process.env.RESEARCHFORGE_E2E_EXPECT_TIMEOUT || "15000") },
  webServer: process.env.CI ? [
    { command: `python -m uvicorn app.main:app --host 127.0.0.1 --port ${e2eApiPort}`, cwd: "../backend", url: `http://127.0.0.1:${e2eApiPort}/health`, timeout: 90000,
      env: { RESEARCHFORGE_ENV: "local", RESEARCHFORGE_AUTH_MODE: "development", RESEARCHFORGE_STORE_BACKEND: "json", RESEARCHFORGE_PERSISTENCE: "1", RESEARCHFORGE_STORE_PATH: ".data/playwright-store.json", RESEARCHFORGE_ALLOW_MOCK_MODELS: "1", RESEARCHFORGE_RATE_LIMIT_BACKEND: "local", RESEARCHFORGE_API_KEY: "" } },
    { command: `npx next dev --hostname 127.0.0.1 --port ${e2eWebPort}`, url: `http://127.0.0.1:${e2eWebPort}/tasks`, timeout: 90000, env: { RESEARCHFORGE_BACKEND_URL: `http://127.0.0.1:${e2eApiPort}` } },
  ] : undefined,
  use: { baseURL: process.env.RESEARCHFORGE_PREVIEW_URL || process.env.RESEARCHFORGE_WEB_URL || `http://127.0.0.1:${e2eWebPort}`, screenshot: "only-on-failure", trace: "retain-on-failure", launchOptions: { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH } },
  projects: [{ name: "desktop", use: { ...devices["Desktop Chrome"] } }, { name: "mobile", use: { ...devices["iPhone 13"], defaultBrowserType: "chromium" } }],
});
