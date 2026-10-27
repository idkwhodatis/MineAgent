import { test, expect } from "@playwright/test";
import { account, api, authorization, token } from "../e2e/helpers";
import { assertFactorLibraryNavigation } from "./factor-library";

function id(name: string) {
  const value = process.env[name];
  if (!value || !/^[0-9a-f-]{36}$/i.test(value))
    throw new Error(`Missing actual result identity ${name}`);
  return value;
}
test("actual configured results require release and survive reload without live authority", async ({
  page,
  request,
}) => {
  const runId = id("E2E_RD_RESULT_RUN_ID"),
    snapshotId = id("E2E_SNAPSHOT_ID"),
    analysisId = id("E2E_ANALYSIS_ID");
  const headers = authorization(await token(request));
  const snapshot = `/engine-runs/${runId}/execution-snapshots/${snapshotId}`;
  const analysis = `/engine-runs/${runId}/analyses/${analysisId}`;
  for (const path of [snapshot + "/values", snapshot + "/export", analysis]) {
    const response = await request.get(api(path), { headers });
    expect(response.status()).toBe(409);
    expect((await response.json()).code).toBe("engine_result_release_required");
  }
  const user = account();
  await page.goto("/login");
  await page.getByLabel("Email address").fill(user.email);
  await page.getByLabel("Password", { exact: true }).fill(user.password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Research overview" }),
  ).toBeVisible();
  const inputResponse = await request.get(api(`/engine-runs/${runId}/inputs`), {
    headers,
  });
  expect(inputResponse.status()).toBe(200);
  const inputHistory = await inputResponse.json();
  const generated = inputHistory.items.filter(
    (item: { origin: string }) => item.origin === "agent_generated",
  );
  expect(generated).toHaveLength(1);
  expect(generated[0].factor_version_id).toBe(id("E2E_FACTOR_VERSION_ID"));
  expect(generated[0].operations.length).toBeGreaterThan(0);
  expect(generated[0].default_pinned).toBe(false);
  await page.goto(`/engine-runs/${runId}`);
  await expect(
    page.getByRole("heading", {
      name: "Agent-generated proposals",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", {
      name: `Open exact proposed factor version ${generated[0].factor_version_id}`,
      exact: true,
    }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("heading", {
      name: "Agent-generated proposals",
      exact: true,
    }),
  ).toBeVisible();
  await assertFactorLibraryNavigation(page, {
    factorId: id("E2E_FACTOR_ID"),
    versionId: id("E2E_FACTOR_VERSION_ID"),
    runId,
    snapshotId,
    analysisId,
  });
  const factorVersionPath = `/factors/${id("E2E_FACTOR_ID")}/versions/${id("E2E_FACTOR_VERSION_ID")}`;
  await page.goto(`${factorVersionPath}?tab=values&execution=${snapshotId}`);
  await expect(
    page.getByRole("heading", {
      name: "Explicit historical result release required",
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Released historical factor values" }),
  ).toHaveCount(0);
  await expect(page.getByLabel("Exact physical snapshot")).toHaveValue("");
  await page.goto(`/engine-runs/${runId}/snapshots/${snapshotId}`);
  await expect(
    page.getByRole("heading", {
      name: "Explicit historical result release required",
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Released historical factor values" }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Release historical result", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Released historical factor values" }),
  ).toBeVisible();
  await expect(
    page.getByText("3 retained rows.", { exact: false }),
  ).toBeVisible();
  const values = await request.get(api(snapshot + "/values"), { headers });
  expect(values.status()).toBe(200);
  const rows = (await values.json()).data;
  expect(rows).toHaveLength(3);
  for (const row of rows)
    await expect(
      page.getByRole("cell", { name: String(row.value), exact: true }),
    ).toBeVisible();
  const download = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "Export historical snapshot JSON" })
    .click();
  expect((await download).suggestedFilename()).toBe(
    `execution-snapshot-${snapshotId}.json`,
  );
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Released historical factor values" }),
  ).toBeVisible();
  await page.goto(`/engine-runs/${runId}/analyses/${analysisId}`);
  await expect(
    page.getByRole("heading", {
      name: "Explicit historical result release required",
    }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Release historical result", exact: true })
    .click();
  await expect(
    page.getByRole("heading", {
      name: "Released direct analysis",
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.getByText("passed", { exact: true })).toBeVisible();
  const result = await request.get(api(analysis), { headers });
  expect(result.status()).toBe(200);
  const data = await result.json();
  expect(data.result.ic).toBeCloseTo(0.9845785700149436, 12);
  expect(data.result.rank_ic).toBe(1);
  expect(data.measurement.value).toBe(1);
  await expect(
    page.getByText(String(data.result.ic), { exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("heading", {
      name: "Released direct analysis",
      exact: true,
    }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Open execution snapshot" }).click();
  await expect(
    page.getByRole("heading", { name: "Released historical factor values" }),
  ).toBeVisible();
  await page.goBack();
  await expect(
    page.getByRole("heading", {
      name: "Released direct analysis",
      exact: true,
    }),
  ).toBeVisible();
  await page.goto(`${factorVersionPath}?tab=values&execution=${snapshotId}`);
  await expect(
    page.getByRole("heading", { name: "Released historical factor values" }),
  ).toBeVisible();
  for (const row of rows)
    await expect(
      page.getByRole("cell", { name: String(row.value), exact: true }),
    ).toBeVisible();
  await page
    .getByRole("link", { name: "Research evaluation", exact: true })
    .click();
  await expect(page).toHaveURL(new RegExp(`execution=${snapshotId}`));
  await expect(
    page.getByRole("heading", {
      name: "Released direct analysis",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByText(String(data.result.ic), { exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("heading", {
      name: "Released direct analysis",
      exact: true,
    }),
  ).toBeVisible();
  await page.goBack();
  await expect(
    page.getByRole("heading", { name: "Released historical factor values" }),
  ).toBeVisible();
  await expect(page.getByLabel("Exact physical snapshot")).toHaveValue("");
  const otherHeaders = authorization(await token(request, true));
  for (const path of [
    snapshot + "/values",
    snapshot + "/export",
    analysis,
    `/engine-runs/${runId}/inputs`,
  ])
    expect(
      (await request.get(api(path), { headers: otherHeaders })).status(),
    ).toBe(404);
  for (const path of [snapshot, analysis])
    expect(
      (
        await request.post(api(path + "/release"), { headers: otherHeaders })
      ).status(),
    ).toBe(404);
});
