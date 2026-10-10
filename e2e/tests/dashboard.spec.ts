import { expect, test } from "@playwright/test";

test.describe("Overview", () => {
  test("shows the case ledger for every suite and target", async ({ page }) => {
    await page.goto("/#/overview");
    await expect(page.getByRole("heading", { name: "Every case, every target" })).toBeVisible();
    for (const suite of ["Accuracy", "Tool use", "Context isolation"]) {
      await expect(page.getByRole("heading", { name: suite, exact: true }).first()).toBeVisible();
    }
    await expect(page.getByRole("list", { name: /^mock-strong: \d+ of 30 passing$/ })).toBeVisible();
    await expect(page.getByRole("list", { name: /^agent-secure: 12 of 12 passing$/ })).toBeVisible();
  });

  test("a failing ledger cell opens that result in the failure explorer", async ({ page }) => {
    await page.goto("/#/overview");
    const leakyRow = page.getByRole("list", { name: /^agent-leaky-memory:/ });
    const failing = leakyRow.locator("button.cell");
    await expect(failing.first()).toBeVisible();
    await failing.first().click();
    await expect(page).toHaveURL(/#\/failures\?.*suite=isolation.*target=agent-leaky-memory/);
    await expect(page.getByRole("heading", { name: "Checks" })).toBeVisible();
    await expect(page.locator("mark.secret").first()).toBeVisible();
    await expect(page.getByText(/unauthorized disclosure/).first()).toBeVisible();
    await expect(page.locator("pre", { hasText: "agentbench repro" })).toBeVisible();
  });
});

test.describe("Failure explorer", () => {
  test("filters by suite and outcome and shows tool-call evidence", async ({ page }) => {
    await page.goto("/#/failures");
    await page.getByLabel("Suite").selectOption("tool_use");
    await page.getByLabel("Target").selectOption("agent-naive-retries");
    const items = page.getByRole("option");
    await expect(items.first()).toBeVisible();
    await page.getByRole("option", { name: /email-timeout-after-commit/ }).click();
    await expect(page.getByRole("heading", { name: "Committed side effects" })).toBeVisible();
    await expect(page.getByText("no duplicate writes")).toBeVisible();
  });

  test("search narrows the list", async ({ page }) => {
    await page.goto("/#/failures?status=all");
    await page.getByRole("searchbox", { name: "Search" }).fill("classification-001");
    await expect(page.getByRole("option").first()).toContainText("classification-001");
  });
});

test.describe("Test lab", () => {
  test("launches a run and links to it when finished", async ({ page }) => {
    await page.goto("/#/lab");
    await expect(page.getByRole("heading", { name: "Test lab" })).toBeVisible();
    // Keep only the Accuracy suite and the cheap mock model.
    for (const target of ["mock-strong", "mock-flaky"]) {
      const box = page.getByRole("checkbox", { name: new RegExp(`^${target} `) });
      if (await box.isChecked()) await box.uncheck();
    }
    const fast = page.getByRole("checkbox", { name: /^mock-fast / });
    if (!(await fast.isChecked())) await fast.check();
    await page.getByLabel("Cases per suite (optional)").fill("5");
    await page.getByLabel("Label").fill("e2e-from-lab");
    await page.getByRole("button", { name: "Estimate cost" }).click();
    await expect(page.getByRole("cell", { name: "mock-fast" })).toBeVisible();
    await page.getByRole("button", { name: "Start run" }).click();
    const view = page.getByRole("link", { name: "View run" }).first();
    await expect(view).toBeVisible({ timeout: 30_000 });
    await view.click();
    await expect(page.getByRole("heading", { name: "e2e-from-lab" })).toBeVisible();
  });
});

test.describe("Regression history", () => {
  test("compares two runs and shows a gate verdict", async ({ page }) => {
    await page.goto("/#/history?suite=core&target=mock-strong");
    await expect(page.getByRole("heading", { name: "Pass rate" })).toBeVisible();
    await page.getByLabel("Baseline").selectOption({ label: /e2e-baseline/ });
    await page.getByLabel("Candidate").selectOption({ label: /e2e-candidate/ });
    await page.getByRole("button", { name: "Compare runs" }).click();
    await expect(page.locator(".verdict b")).toHaveText(/Pass|Investigate|Blocked/);
    await expect(page.getByRole("cell", { name: "core.accuracy" }).first()).toBeVisible();
  });
});
