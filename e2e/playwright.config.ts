import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

// The dashboard runs against a throwaway SQLite database seeded by seed.sh with fast mock runs.
const repo = path.resolve(__dirname, "..");
const db = path.join(__dirname, ".data", "e2e.db");
const env = {
  AGENTBENCH_DATABASE_URL: `sqlite:///${db}`,
  AGENTBENCH_MOCK_TIME_SCALE: "0",
  PYTHONUNBUFFERED: "1",
};

// Use a pre-installed Chromium when browser downloads are not possible (set PW_CHROMIUM_PATH).
const executablePath = process.env.PW_CHROMIUM_PATH || undefined;

export default defineConfig({
  testDir: "./tests",
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }], ["junit", { outputFile: "results/junit.xml" }]] : "list",
  use: {
    baseURL: "http://127.0.0.1:8765",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    launchOptions: executablePath ? { executablePath, args: (process.env.PW_CHROMIUM_ARGS || "").split(" ").filter(Boolean) } : {},
  },
  projects: [
    {
      name: "chromium",
      testIgnore: /.*\.api\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 1000 } },
    },
    { name: "api", testMatch: /.*\.api\.spec\.ts/, use: {} },
  ],
  webServer: [
    {
      command: `bash ${path.join(__dirname, "seed.sh")} && agentbench serve --port 8765 --config ${path.join(repo, "configs", "agentbench.yaml")}`,
      cwd: repo,
      url: "http://127.0.0.1:8765/api/health",
      env,
      timeout: 180_000,
      reuseExistingServer: !process.env.CI,
    },
    {
      command: `agentbench agent serve --port 8766 --preset secure --config ${path.join(repo, "configs", "agentbench.yaml")}`,
      cwd: repo,
      url: "http://127.0.0.1:8766/health",
      env,
      timeout: 60_000,
      reuseExistingServer: !process.env.CI,
    },
  ],
});
