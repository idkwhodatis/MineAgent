import { test, expect, type Request } from "@playwright/test";
import type { DirectDay } from "../../src/client/types.gen";
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
    page
      .getByText("Mean IC", { exact: true })
      .locator("xpath=following-sibling::dd[1]"),
  ).toHaveText(String(data.result.ic));

  // Read real retained rows; do not synthesize nulls or mock result transport.
  const days: DirectDay[] = data.result.days;
  expect(days.length).toBeGreaterThan(0);
  const diagnostics = page.getByRole("region", {
    name: "Retained daily factor diagnostics",
  });
  const dailyTable = diagnostics.getByRole("table", {
    name: "Exact retained daily observations",
  });
  await expect(dailyTable).toBeVisible();
  await expect(dailyTable.getByRole("row")).toHaveCount(days.length + 1);
  for (const day of days) {
    const row = dailyTable.getByRole("row").filter({
      has: page.getByRole("cell", { name: day.session, exact: true }),
    });
    expect((await row.getByRole("cell").allTextContents()).slice(-5)).toEqual([
      day.session,
      day.ic === null ? "Not defined" : String(day.ic),
      day.rank_ic === null ? "Not defined" : String(day.rank_ic),
      String(day.pair_count),
      day.reason,
    ]);
  }
  for (const metric of ["ic", "rank_ic"] as const) {
    const chart = diagnostics.locator(
      `svg[aria-label="${metric === "ic" ? "Daily IC" : "Daily rank IC"}"]`,
    );
    await expect(chart).toBeVisible();
    // Every null breaks the real retained series; no invented zero point or
    // interpolation across a missing observation is permitted. Fixtures with
    // no nulls test their actual contiguous series, not a fabricated null case.
    const expectedSegments: number[] = [];
    let length = 0;
    for (const day of days) {
      if (day[metric] !== null) length += 1;
      else if (length) {
        expectedSegments.push(length);
        length = 0;
      }
    }
    if (length) expectedSegments.push(length);
    const actualSegments = await chart
      .locator("polyline")
      .evaluateAll((lines) =>
        lines.map(
          (line) =>
            (line.getAttribute("points") ?? "").trim().split(/\s+/).length,
        ),
      );
    expect(actualSegments).toEqual(expectedSegments);
    await expect(chart.locator("circle")).toHaveCount(
      days.filter((day) => day[metric] !== null).length,
    );
    for (const day of days) {
      const value = day[metric];
      if (value !== null) {
        const point = chart.getByRole("button", {
          name: `Select ${day.session}: ${metric === "ic" ? "Daily IC" : "Daily rank IC"} ${value}`,
          exact: true,
        });
        await expect(point).toHaveAttribute(
          "cy",
          String(16 + ((1 - value) / 2) * 148),
        );
      }
    }
  }
  await expect(
    diagnostics.locator('svg[aria-label="Daily paired observations"]'),
  ).toBeVisible();
  await expect(
    diagnostics.getByText("Group-label spread: not computed", { exact: false }),
  ).toBeVisible();
  await expect(
    diagnostics.getByText("no denominator is inferred", { exact: false }),
  ).toBeVisible();

  const selectedDay = days.find((day) => day.ic !== null);
  expect(selectedDay).toBeDefined();
  const chosen = selectedDay!;
  const point = diagnostics
    .locator('svg[aria-label="Daily IC"]')
    .getByRole("button", {
      name: `Select ${chosen.session}: Daily IC ${chosen.ic}`,
      exact: true,
    });
  await point.focus();
  await point.press("Enter");
  await expect(point).toHaveAttribute("aria-pressed", "true");
  await expect(
    diagnostics.getByLabel(
      `Use session ${chosen.session} as guidance context`,
      { exact: true },
    ),
  ).toBeChecked();

  const mutations: string[] = [];
  const observeMutation = (request: Request) => {
    if (
      !["GET", "HEAD", "OPTIONS"].includes(request.method()) &&
      new URL(request.url()).pathname.includes("/api/v1/")
    ) {
      mutations.push(`${request.method()} ${new URL(request.url()).pathname}`);
    }
  };
  page.on("request", observeMutation);
  try {
    await diagnostics
      .getByRole("button", {
        name: "Prepare guidance from selected sessions",
        exact: true,
      })
      .click();
    const review = page.getByRole("region", {
      name: "Review observation-bound guidance",
    });
    await expect(review).toBeVisible();
    for (const exact of [
      runId,
      analysisId,
      snapshotId,
      data.result_sha256,
      data.measurement_sha256,
      data.result.protocol_sha256,
      chosen.session,
    ]) {
      await expect(review.locator("pre")).toContainText(exact);
    }
    const save = review.getByRole("button", {
      name: "Save observation-bound steer version",
      exact: true,
    });
    await expect(save).toBeDisabled();
    await review
      .getByLabel("Your research direction", { exact: true })
      .fill(
        "Review this retained session before the next eligible research stage.",
      );
    await review
      .getByLabel("I reviewed these exact references and my guidance.", {
        exact: true,
      })
      .check();
    await expect(save).toBeEnabled();
    // Deliberately do not save: the fixture's inputs and version counts stay intact.
    await review
      .getByRole("button", { name: "Close observation draft", exact: true })
      .click();
    await expect(review).toHaveCount(0);
    expect(mutations).toEqual([]);
  } finally {
    page.off("request", observeMutation);
  }
  await page.reload();
  await expect(
    page.getByRole("heading", {
      name: "Released direct analysis",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Review observation-bound guidance" }),
  ).toHaveCount(0);
  await expect(
    page.getByLabel(`Use session ${chosen.session} as guidance context`, {
      exact: true,
    }),
  ).not.toBeChecked();
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
    page
      .getByText("Mean IC", { exact: true })
      .locator("xpath=following-sibling::dd[1]"),
  ).toHaveText(String(data.result.ic));
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
