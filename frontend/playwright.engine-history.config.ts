import { defineConfig, devices } from "@playwright/test";

// Explicit real-stack gate. No route interception, storage mocks or silent skip.
export default defineConfig({
  testDir: "./tests/engine-history-acceptance",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  workers: 1,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://127.0.0.1:5173",
    ...devices["Desktop Chrome"],
    trace: "off",
    video: "off",
    screenshot: "off",
  },
});
