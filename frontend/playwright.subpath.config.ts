import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/subpath",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  workers: 1,
  retries: 0,
  reporter: [["junit", { outputFile: process.env.E2E_SUBPATH_REPORT }]],
  use: {
    baseURL: process.env.E2E_SUBPATH_ORIGIN,
    trace: "off",
    video: "off",
    screenshot: "off",
    actionTimeout: 15_000,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
