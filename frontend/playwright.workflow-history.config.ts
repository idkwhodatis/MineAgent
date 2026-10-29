import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./tests/workflow-history-acceptance",
  testMatch: [
    "history.spec.ts",
    "create.spec.ts",
    "observation-checkpoint.spec.ts",
  ],
  timeout: 90_000,
  expect: { timeout: 15_000 },
  workers: 1,
  retries: 0,
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://127.0.0.1:5173",
    ...devices["Desktop Chrome"],
    trace: "off",
    video: "off",
    screenshot: "off",
  },
});
