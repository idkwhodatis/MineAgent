import { test, expect } from "@playwright/test";
import { account, api, authorization, token } from "../e2e/helpers";

const prefix = process.env.E2E_APP_SUBPATH || "";
function savedRun() {
  const id = process.env.E2E_RD_HISTORY_RUN_ID;
  if (!id || !/^[0-9a-f-]{36}$/i.test(id))
    throw new Error(
      "Requires an actual owner-bound revision018 persisted RD source run in E2E_RD_HISTORY_RUN_ID; never substitute a mock or claim execution success.",
    );
  return id;
}

test("actual saved RD history survives reload, back and owner denial without enabling execution", async ({
  page,
  request,
}) => {
  const id = savedRun();
  const auth = await token(request);
  const detail = await request.get(api(`/engine-runs/${id}`), {
    headers: authorization(auth),
  });
  expect(detail.ok()).toBe(true);
  const history = await request.get(api(`/engine-runs/${id}/history`), {
    headers: authorization(auth),
  });
  expect(history.ok()).toBe(true);
  const persisted = (await history.json()).data.find(
    (row: { source_id: string | null }) => row.source_id,
  );
  expect(
    persisted,
    "Gate needs a real persisted source, not an empty list",
  ).toBeTruthy();
  const user = account();
  await page.goto(`${prefix}/login`);
  await page.getByLabel("Email address").fill(user.email);
  await page.getByLabel("Password", { exact: true }).fill(user.password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Research overview" }),
  ).toBeVisible();
  await page.goto(`${prefix}/engine-runs/preflight`);
  await expect(
    page.getByRole("heading", {
      name: "Check RD-Agent research eligibility",
      exact: true,
    }),
  ).toBeVisible();
  const datasetId = process.env.E2E_RD_PREFLIGHT_DATASET_ID;
  expect(datasetId, "Real retained dataset fixture required").toBeTruthy();
  await page
    .getByLabel("Retained dataset", { exact: true })
    .selectOption(datasetId!);
  await page.getByLabel("Output window start (inclusive)").fill("2024-03-13");
  await page.getByLabel("Output window end (exclusive)").fill("2024-03-14");
  await page.getByLabel("Task identifier").fill("browser-owned-preflight");
  await page
    .getByLabel("Task description")
    .fill("Check retained source eligibility only");
  await page.getByLabel("Factor formulation").fill("close[t]/close[t-1]-1");
  await page.getByLabel("Output unit identifier").fill("return");
  await page.getByLabel("Factor kind").selectOption("time_series");
  await page.getByLabel("Threshold", { exact: true }).fill("0.5");
  await page.getByLabel(/I declare daily session-close availability/).check();
  const checkedResponse = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname.endsWith(
        "/api/v1/engine-runs/preflight",
      ) && response.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Check eligibility", exact: true })
    .click();
  const checked = await (await checkedResponse).json();
  await expect(
    page.getByText("Retained data eligible.", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByText("runtime_disabled", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Create run — not enabled" }),
  ).toBeDisabled();
  // An edit invalidates the result; oversized workload must be rejected by server.
  await page.getByLabel("Iterations", { exact: true }).fill("2");
  await expect(
    page.getByRole("heading", { name: "Authoritative preflight result" }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Check eligibility", exact: true })
    .click();
  await expect(
    page.getByText("output_window_unsupported", { exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Authoritative preflight result" }),
  ).toHaveCount(0);
  await page.getByRole("link", { name: "Back to engine history" }).click();
  await expect(
    page.getByRole("heading", { name: "Engine research history" }),
  ).toBeVisible();
  await page.goto(`${prefix}/engine-runs/${id}?offset=0`);
  await expect(
    page.getByText(persisted.source_id, { exact: true }).first(),
  ).toBeVisible();
  await expect(
    page.getByText("Strategy: not applicable to factor research"),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByText(persisted.source_id, { exact: true }).first(),
  ).toBeVisible();
  await page.getByRole("link", { name: "All engine history" }).click();
  await expect(
    page.getByRole("heading", { name: "Engine research history" }),
  ).toBeVisible();
  await expect(
    page.getByText("Creation disabled", { exact: false }).first(),
  ).toBeVisible();
  await page.goBack();
  await expect(
    page.getByText(persisted.source_id, { exact: true }).first(),
  ).toBeVisible();
  await page.goto(
    `${prefix}/engine-runs/${id}/snapshots/00000000-0000-0000-0000-000000000000`,
  );
  await expect(
    page.getByRole("heading", {
      name: "Execution snapshot metadata",
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.getByText(/not found/i).first()).toBeVisible();
  await page
    .getByRole("link", { name: "Back to research history", exact: true })
    .click();
  await expect(
    page.getByText(persisted.source_id, { exact: true }).first(),
  ).toBeVisible();
  const other = await token(request, true);
  for (const suffix of ["", "/history"]) {
    expect(
      (
        await request.get(api(`/engine-runs/${id}${suffix}`), {
          headers: authorization(other),
        })
      ).status(),
    ).toBe(404);
  }
  expect(
    (
      await request.post(api("/engine-runs"), {
        headers: {
          ...authorization(auth),
          "Idempotency-Key": crypto.randomUUID(),
        },
        data: {
          ...checked.normalized_request,
          preflight_sha256: checked.preflight_sha256,
        },
      })
    ).status(),
  ).toBe(409);
  await page.goto(`${prefix}/engine-runs/00000000-0000-0000-0000-000000000000`);
  await expect(
    page.getByRole("heading", { name: "Seed evaluation" }),
  ).toHaveCount(0);
  await expect(page.getByText(/not found/i).first()).toBeVisible();
});
