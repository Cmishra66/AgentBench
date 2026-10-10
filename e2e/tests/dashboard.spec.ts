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
    const items = page.getByRole("listbox", { name: "Results" }).getByRole("option");
    await expect(items.first()).toBeVisible();
    await page.getByRole("option", { name: /email-timeout-after-commit/ }).click();
    await expect(page.getByRole("heading", { name: "Committed side effects" })).toBeVisible();
    await expect(page.getByText("no duplicate writes")).toBeVisible();
  });

  test("search narrows the list", async ({ page }) => {
    await page.goto("/#/failures?status=all");
    await page.getByRole("searchbox", { name: "Search" }).fill("classification-001");
    await expect(page.getByRole("listbox", { name: "Results" }).getByRole("option").first()).toContainText("classification-001");
  });
});

test.describe("Test lab", () => {
  test("selects distinct Nex and Nemotron API models for comparison", async ({ page }) => {
    await page.route("**/api/config", async (route) => {
      const response = await route.fetch();
      const config = await response.json();
      for (const target of config.targets) {
        if (["openrouter", "nemotron-ultra"].includes(target.id)) target.available = true;
      }
      const nex = config.targets.find((target: { id: string }) => target.id === "openrouter");
      nex.model = "nex-agi/nex-n2.5-mini";
      const index = config.targets.findIndex((target: { id: string }) => target.id === "nemotron-ultra");
      config.targets[index].model = "nvidia/nemotron-3-ultra-550b-a55b:free";
      config.targets.splice(index, 0, { ...nex, id: "nex-alias" });
      await route.fulfill({ response, json: config });
    });
    await page.goto("/#/lab");
    await page.getByRole("checkbox", { name: /^Accuracy / }).uncheck();
    await page.getByRole("checkbox", { name: /^Load and performance / }).check();
    await page.getByRole("button", { name: "Compare API models", exact: true }).click();
    const selected = page.getByRole("region", { name: "Targets for this run", exact: true });
    await expect(selected).toContainText("openrouter (API: nex-agi/nex-n2.5-mini)");
    await expect(selected).toContainText("nemotron-ultra (API: nvidia/nemotron-3-ultra-550b-a55b:free)");
    await expect(page.getByRole("checkbox", { name: /^openrouter / })).toBeChecked();
    await expect(page.getByRole("checkbox", { name: /^nemotron-ultra / })).toBeChecked();
    await expect(page.getByRole("checkbox", { name: /^nex-alias / })).not.toBeChecked();
    await expect(page.getByRole("checkbox", { name: /^mock-strong / })).not.toBeChecked();
  });

  test("compares load targets and shows limits, queueing and stage objectives", async ({ page }) => {
    await page.goto("/#/lab");
    await page.getByRole("checkbox", { name: /^Accuracy / }).uncheck();
    await page.getByRole("checkbox", { name: /^Load and performance / }).check();
    const modelChoices = page.locator('section[aria-labelledby="t-model"] input[type="checkbox"]');
    for (const box of await modelChoices.all()) if (await box.isChecked()) await box.uncheck();
    await page.getByRole("checkbox", { name: /^mock-strong / }).check();
    await page.getByRole("checkbox", { name: /^mock-fast / }).check();
    await page.getByLabel("Stage 1 duration in seconds").fill("0.2");
    await page.getByLabel("Max requests per target").fill("6");
    await page.getByLabel("Max simultaneous calls").fill("2");
    await page.getByLabel("Minimum samples per stage").fill("1");
    await page.getByLabel("Label", { exact: true }).fill("e2e-load-comparison");
    await page.getByRole("button", { name: "Start run" }).click();
    await expect(page.getByText(/^Targets:.*mock-fast.*simulated/).first()).toBeVisible();
    await page.getByRole("region", { name: "Run settings", exact: true }).getByRole("link", { name: "View run" }).click();
    const comparison = page.getByRole("table", { name: "Load comparison", exact: true });
    await expect(comparison.getByRole("columnheader", { name: "mock-fast" })).toBeVisible();
    await expect(comparison.getByRole("columnheader", { name: "mock-strong" })).toBeVisible();
    await expect(comparison.getByRole("cell", { name: "Client queue p95", exact: true })).toBeVisible();
    await expect(comparison.getByRole("cell", { name: "Correct answers per second", exact: true })).toBeVisible();
    await expect(comparison.getByRole("cell", { name: "Observed peak calls", exact: true })).toBeVisible();
  });

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
    await page.getByLabel("Label", { exact: true }).fill("e2e-from-lab");
    await page.getByRole("button", { name: "Estimate cost" }).click();
    await expect(page.getByRole("cell", { name: "mock-fast" })).toBeVisible();
    await page.getByRole("button", { name: "Start run" }).click();
    const view = page.getByRole("region", { name: "Run settings", exact: true }).getByRole("link", { name: "View run" });
    await expect(view).toBeVisible({ timeout: 30_000 });
    await view.click();
    await expect(page.getByRole("heading", { name: "e2e-from-lab" })).toBeVisible();
  });
});

test.describe("Regression history", () => {
  test("compares two runs and shows a gate verdict", async ({ page }) => {
    await page.goto("/#/history?suite=core&target=mock-strong");
    await expect(page.getByRole("heading", { name: "Pass rate" })).toBeVisible();
    for (const [field, label] of [["Baseline", "e2e-baseline"], ["Candidate", "e2e-candidate"]]) {
      const select = page.getByRole("combobox", { name: field, exact: true });
      const value = await select.locator("option").filter({ hasText: label }).getAttribute("value");
      expect(value).toBeTruthy();
      await select.selectOption(value!);
    }
    await page.getByRole("button", { name: "Compare runs" }).click();
    await expect(page.locator(".verdict b")).toHaveText(/Pass|Investigate|Blocked/);
    await expect(page.getByRole("cell", { name: "core.accuracy" }).first()).toBeVisible();
  });
});
