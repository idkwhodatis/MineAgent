import { test, expect, type APIRequestContext } from "@playwright/test";
import { api, authorization, signIn, token } from "./helpers";
import type { Token } from "../../src/client/types.gen";
import type {
  FactorDetail,
  FactorSnapshot,
  FactorValues,
  FactorVersion,
} from "../../src/lib/factor-api";

type Fixture = {
  factor: { id: string };
  version: FactorVersion;
  snapshot: FactorSnapshot;
};
async function fixture(
  request: APIRequestContext,
  auth: Token,
  parent?: Fixture,
  kind = "momentum",
): Promise<Fixture> {
  const response = await request.post(api("/factor-fixtures"), {
    headers: { ...authorization(auth), "Idempotency-Key": crypto.randomUUID() },
    data: {
      kind,
      name: `Browser factor ${crypto.randomUUID()}`,
      ...(parent
        ? { factor_id: parent.factor.id, parent_version_id: parent.version.id }
        : {}),
      lookback_sessions: 2,
      lag_sessions: 0,
    },
  });
  expect(
    response.status(),
    "Requires the real persisted fixed-factor fixture admission, not intercepted routes",
  ).toBe(201);
  return response.json() as Promise<Fixture>;
}
const href = (value: Fixture, tab = "values") =>
  `/factors/${value.factor.id}/versions/${value.version.id}?snapshot=${value.snapshot.id}&tab=${tab}`;

test.beforeAll(async ({ request }) => {
  const auth = await token(request);
  const capability = await request.get(api("/factors/capabilities"), {
    headers: authorization(auth),
  });
  expect(capability.ok()).toBe(true);
  expect(
    (await capability.json()).enabled,
    "Stage1 browser acceptance requires FACTOR_LIBRARY_ENABLED=true on the real fixture stack; deployment default remains false",
  ).toBe(true);
});

test("real persisted factor values, missingness and sample survive exact URL refresh and history", async ({
  page,
  request,
}) => {
  const auth = await token(request);
  const root = await fixture(request, auth);
  const child = await fixture(request, auth, root, "risk_adjusted_momentum");
  const values = await request.get(
    api(`/factor-snapshots/${root.snapshot.id}/values`),
    { headers: authorization(auth) },
  );
  expect(values.ok()).toBe(true);
  const rows = (await values.json()) as FactorValues;
  const observation = rows.data.find(
    (row) =>
      row.session_date === "2024-03-08" &&
      row.listing_id === "fixture:XNAS:AAA",
  );
  expect(observation?.value).toBeCloseTo(0.21, 12);
  expect(
    rows.data.some((row) => row.value === null && row.missing_reason),
  ).toBe(true);
  await signIn(page);
  await page.goto(href(root));
  await expect(page.getByTestId("factor-values")).toContainText("AAA");
  const row = page
    .getByTestId("factor-values")
    .getByRole("row")
    .filter({ hasText: "2024-03-08" })
    .filter({ hasText: "AAA" });
  await row.getByRole("button", { name: "Inspect sample" }).click();
  await expect(page.getByTestId("factor-sample")).toContainText(
    "fixed_fixture_recomputed",
  );
  await expect(page.getByTestId("factor-sample")).toContainText(
    "Actual saved input window",
  );
  await expect(page).toHaveURL(/listing=fixture/);
  const selected = page.url();
  await page.reload();
  await expect(page.getByTestId("factor-sample")).toContainText("momentum");
  await page
    .getByLabel("Exact factor version", { exact: true })
    .selectOption(child.version.id);
  await expect(page.getByLabel("Exact physical snapshot")).toHaveValue("");
  await expect(page.getByTestId("factor-values")).toHaveCount(0);
  await page
    .getByLabel("Exact physical snapshot")
    .selectOption(child.snapshot.id);
  await expect(page.getByTestId("factor-values")).toContainText(
    "zero_start_price_or_daily_volatility",
  );
  await page.goBack();
  await expect(page.getByLabel("Exact physical snapshot")).toHaveValue("");
  await page.goBack();
  await expect(page).toHaveURL(selected);
  await expect(page.getByTestId("factor-sample")).toContainText("momentum");
  await page.goForward();
  await expect(
    page.getByLabel("Exact factor version", { exact: true }),
  ).toHaveValue(child.version.id);
});

test("real parent comparison keeps incompatible bilateral values and suppresses arithmetic", async ({
  page,
  request,
}) => {
  const auth = await token(request);
  const root = await fixture(request, auth);
  const child = await fixture(request, auth, root, "risk_adjusted_momentum");
  await signIn(page);
  await page.goto(href(child, "evolution"));
  await page.getByLabel("Left exact snapshot").selectOption(root.snapshot.id);
  await page.getByRole("button", { name: "Compare exact versions" }).click();
  await expect(
    page.getByRole("region", { name: "Factor comparison result" }),
  ).toContainText("incompatible");
  await expect(page.getByTestId("factor-comparison-values")).toContainText(
    "incompatible unit scale",
  );
  await expect(page.getByTestId("factor-comparison-values")).toContainText(
    "zero_start_price_or_daily_volatility",
  );
  await page.getByRole("link", { name: "Strategy usage", exact: true }).click();
  await expect(
    page.getByText("Explicit host-recorded reads", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("No strategy usage is inferred", { exact: false }),
  ).toBeVisible();
  await page
    .getByRole("link", { name: "Research evaluation", exact: true })
    .click();
  await expect(
    page.getByText("No Qlib metric is inferred", { exact: false }),
  ).toBeVisible();
});

test("manual create and child source edits are immutable, repeat clicks are idempotent, presentation leaves source intact", async ({
  page,
  request,
}) => {
  const auth = await token(request);
  await signIn(page);
  await page.goto("/factors");
  await page.getByRole("button", { name: "New factor definition" }).click();
  const name = `Independent definition ${crypto.randomUUID()}`;
  const source = "def factor(close):\n    return close[-1] / close[0] - 1\n";
  await page.getByLabel("Factor name", { exact: true }).fill(name);
  await page.getByLabel("Exact Python source", { exact: true }).fill(source);
  await page
    .getByRole("button", { name: "Save exact definition" })
    .evaluate((button: HTMLButtonElement) => {
      button.click();
      button.click();
    });
  await expect(page).toHaveURL(/\/factors\/[^/]+\/versions\//);
  const match = new URL(page.url()).pathname.match(
    /\/factors\/([^/]+)\/versions\/([^/]+)/,
  )!;
  const [factorId, versionId] = match.slice(1);
  const detailResponse = await request.get(
    api(`/factors/${factorId}/versions/${versionId}`),
    { headers: authorization(auth) },
  );
  const detail = (await detailResponse.json()) as FactorDetail;
  expect(detail.version.code).toBe(source);
  expect(detail.snapshots).toEqual([]);
  const history = await request.get(api(`/factors/${factorId}/versions`), {
    headers: authorization(auth),
  });
  expect((await history.json()).count).toBe(1);
  await page
    .getByRole("button", { name: "Bookmark factor", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Remove bookmark", exact: true }),
  ).toBeVisible();
  await page
    .getByLabel("Display name", { exact: true })
    .fill(`${name} renamed`);
  await page
    .getByRole("button", { name: "Rename factor", exact: true })
    .click();
  await page
    .getByRole("link", { name: "Code & evidence", exact: true })
    .click();
  await expect(page.getByTestId("factor-source")).toHaveText(source);
  const unchanged = await request.get(
    api(`/factors/${factorId}/versions/${versionId}`),
    { headers: authorization(auth) },
  );
  expect((await unchanged.json()).version.definition_sha256).toBe(
    detail.version.definition_sha256,
  );
  await page
    .getByRole("button", { name: "Create child version", exact: true })
    .click();
  await page
    .getByLabel("Exact Python source", { exact: true })
    .fill(`${source}\n# independently retained child\n`);
  await page
    .getByRole("button", { name: "Save exact definition", exact: true })
    .click();
  await expect(page).not.toHaveURL(new RegExp(`/versions/${versionId}`));
  await page.getByRole("link", { name: "Evolution", exact: true }).click();
  await expect(
    page.getByText(`parent ${versionId}`, { exact: false }),
  ).toBeVisible();
});

test("exact export and bundle use real API; foreign resources fail and import capability is explicit", async ({
  page,
  request,
}) => {
  const auth = await token(request);
  const root = await fixture(request, auth);
  await signIn(page);
  await page.goto(href(root, "evidence"));
  await page.getByLabel("Include selected snapshot values").check();
  const downloadPromise = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "Export exact factor package", exact: true })
    .click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe(`factor-${root.version.id}.zip`);
  expect(await download.failure()).toBeNull();
  await page
    .getByLabel("Fixed bundle name", { exact: true })
    .fill("Exact selected factor");
  const bundleResponse = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/v1/factor-bundles" &&
      response.request().method() === "POST",
  );
  await page
    .getByRole("button", {
      name: "Save selected version as bundle",
      exact: true,
    })
    .click();
  const bundle = await (await bundleResponse).json();
  expect(bundle.definition.members).toEqual([
    {
      factor_version_id: root.version.id,
      definition_sha256: root.version.definition_sha256,
    },
  ]);
  const other = await token(request, true);
  for (const path of [
    `/factors/${root.factor.id}/versions/${root.version.id}`,
    `/factor-snapshots/${root.snapshot.id}/values`,
  ]) {
    const denied = await request.get(api(path), {
      headers: authorization(other),
    });
    expect(denied.status()).toBe(404);
  }
  const capability = await request.get(api("/factors/capabilities"), {
    headers: authorization(auth),
  });
  const enabled = (await capability.json()).package_import_enabled;
  expect(enabled).toBe(process.env.E2E_FACTOR_IMPORT_ACCEPTANCE === "1");
  await page.goto("/factors");
  if (!enabled) {
    const closed = await request.post(api("/factor-packages/import"), {
      headers: {
        ...authorization(auth),
        "Content-Type": "application/zip",
        "Idempotency-Key": crypto.randomUUID(),
      },
      data: Buffer.from("not an accepted archive"),
    });
    expect(closed.status()).toBe(503);
    expect((await closed.json()).code).toBe("factor_import_unavailable");
    await expect(
      page.getByText("Binary package import is unavailable", { exact: false }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Import exact package", exact: true }),
    ).toHaveCount(0);
  } else {
    await expect(
      page.getByLabel("Raw factor ZIP package", { exact: true }),
    ).toBeVisible();
  }
});

if (process.env.E2E_FACTOR_IMPORT_ACCEPTANCE === "1") {
  test("isolated package import preserves source evidence, derives exact local values and survives reload and retry", async ({
    page,
    request,
  }) => {
    const auth = await token(request);
    const root = await fixture(request, auth);
    const exported = await request.post(
      api(`/factor-versions/${root.version.id}/exports`),
      {
        headers: {
          ...authorization(auth),
          "Idempotency-Key": crypto.randomUUID(),
        },
        data: {
          snapshot_ids: [root.snapshot.id],
          evaluation_ids: [],
          include_values: true,
        },
      },
    );
    expect(exported.status()).toBe(201);
    const packageRecord = await exported.json();
    const download = await request.get(
      api(`/factor-packages/${packageRecord.id}/download`),
      { headers: authorization(auth) },
    );
    expect(download.ok()).toBe(true);
    const archive = {
      name: "persisted-factor.zip",
      mimeType: "application/zip",
      buffer: await download.body(),
    };
    await signIn(page);
    await page.goto("/factors");
    await page
      .getByLabel("Raw factor ZIP package", { exact: true })
      .setInputFiles(archive);
    const accepted = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === "/api/v1/factor-packages/import" &&
        response.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "Import exact package", exact: true })
      .click();
    const admissionResponse = await accepted;
    expect(admissionResponse.status()).toBe(202);
    const admission = await admissionResponse.json();
    expect(admission.archive_sha256).toBe(packageRecord.archive_sha256);
    await expect(page).toHaveURL(new RegExp(`import_id=${admission.id}`));
    await page
      .getByRole("button", { name: "Close status view", exact: true })
      .click();
    await expect(
      page.getByRole("region", { name: "Exact import status" }),
    ).toHaveCount(0);
    await expect(page).not.toHaveURL(/import_id=/);
    await page.goto(`/factors?import_id=${admission.id}`);
    await expect(
      page.getByRole("link", {
        name: "Open exact imported factor",
        exact: true,
      }),
    ).toBeVisible({ timeout: 90000 });
    const status = await request.get(api(`/factor-imports/${admission.id}`), {
      headers: authorization(auth),
    });
    expect(status.ok()).toBe(true);
    const outcome = await status.json();
    expect(outcome.status).toBe("succeeded");
    expect(outcome.history_completeness).toBe("unknown");
    expect(outcome.local_bindings.source_version_id).toBe(root.version.id);
    expect(outcome.local_bindings.version_id).not.toBe(root.version.id);
    const mapped = outcome.local_bindings.snapshots.find(
      (item: { source_snapshot_id: string }) =>
        item.source_snapshot_id === root.snapshot.id,
    );
    expect(mapped.status).toBe("derived_validated");
    expect(mapped.snapshot_id).not.toBe(root.snapshot.id);
    await page.reload();
    await expect(
      page.getByRole("link", {
        name: "Open exact imported factor",
        exact: true,
      }),
    ).toBeVisible();
    await page
      .getByRole("link", {
        name: `Open derived snapshot · ${mapped.snapshot_id}`,
        exact: true,
      })
      .click();
    await expect(page.getByTestId("factor-values")).toContainText(
      "fixture:XNAS:AAA",
    );
    const importedValues = await request.get(
      api(
        `/factor-snapshots/${mapped.snapshot_id}/values?listing_id=fixture%3AXNAS%3AAAA&start=2024-03-08&end=2024-03-09`,
      ),
      { headers: authorization(auth) },
    );
    expect(importedValues.ok()).toBe(true);
    expect((await importedValues.json()).data[0].value).toBeCloseTo(0.21, 12);
    const original = await request.get(
      api(`/factors/${root.factor.id}/versions/${root.version.id}`),
      { headers: authorization(auth) },
    );
    expect((await original.json()).version.code_sha256).toBe(
      root.version.code_sha256,
    );
    await page.goto(`/factors?import_id=${admission.id}`);
    await page
      .getByLabel("Raw factor ZIP package", { exact: true })
      .setInputFiles(archive);
    const repeated = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === "/api/v1/factor-packages/import" &&
        response.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "Import exact package", exact: true })
      .click();
    expect((await (await repeated).json()).id).toBe(admission.id);
    const other = await token(request, true);
    const denied = await request.get(api(`/factor-imports/${admission.id}`), {
      headers: authorization(other),
    });
    expect(denied.status()).toBe(404);
  });
}
