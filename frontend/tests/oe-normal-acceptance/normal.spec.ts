import { writeFileSync } from "node:fs";
import { test, expect, type Page } from "@playwright/test";
import { z } from "zod";
import { api, authorization, signIn, token } from "../e2e/helpers";
import type {
  ProviderStructuredSearchRequest,
  ProviderStructuredPreflight,
  ProviderStructuredSubmissionReceipt,
  ProviderStructuredRunView,
  StructuredDemoReceipt,
  StructuredSnapshotPage,
  NormalExecutionRecord,
  NormalExecutionView,
  NormalPairComparison,
  ResearchNoteRevision,
} from "../../src/client/types.gen";
import { oeBudgetFields } from "../../src/features/oe-provider-parameters";

const manifest = z.discriminatedUnion("mode", [
  z.strictObject({
    mode: z.enum(["normal_notes", "normal_notes_edit"]),
    run_id: z.uuid(),
    execution_id: z.uuid(),
    revisions: z
      .array(
        z.strictObject({
          path: z.string().startsWith("/research-notes/"),
          revision_sha256: z.string().regex(/^[a-f0-9]{64}$/),
          restore_href: z
            .string()
            .startsWith("/engine-runs/structured-search/runs/"),
        }),
      )
      .min(2)
      .max(4),
  }),
  z.strictObject({ mode: z.literal("demo") }),
  z.strictObject({
    mode: z.literal("submit"),
    request: z.record(z.string(), z.unknown()),
  }),
  z.strictObject({ mode: z.literal("status"), run_id: z.uuid() }),
  z.strictObject({
    mode: z.literal("results"),
    run_id: z.uuid(),
    execution_ids: z.tuple([z.uuid(), z.uuid()]),
  }),
]);
function observations() {
  return manifest.parse(JSON.parse(process.env.E2E_OE_OBSERVATIONS ?? "null"));
}
function save(value: unknown) {
  const output = process.env.E2E_OE_OUTPUT;
  if (!output) throw new Error("Private observed output path required");
  const raw = JSON.stringify(value);
  expect(Buffer.byteLength(raw)).toBeLessThanOrEqual(131072);
  writeFileSync(output, raw, { encoding: "utf8", flag: "wx", mode: 0o600 });
}
function mutations(page: Page) {
  const calls: string[] = [];
  page.on("request", (req) => {
    if (
      req.url().includes("/api/v1/") &&
      !["GET", "HEAD", "OPTIONS"].includes(req.method())
    )
      calls.push(`${req.method()} ${new URL(req.url()).pathname}`);
  });
  return calls;
}

test("empty owner explicitly creates demo and applies RD inputs without submitting", async ({
  page,
  request,
}) => {
  expect(observations().mode).toBe("demo");
  const headers = authorization(await token(request));
  const before = await request.get(
    api("/engine-runs/datasets?offset=0&limit=25"),
    { headers },
  );
  expect(before.status()).toBe(200);
  expect((await before.json()).count).toBe(0);
  await signIn(page);
  const calls = mutations(page);
  await page.goto("/engine-runs/preflight");
  const create = page.getByRole("button", {
    name: "Create synthetic demo dataset",
    exact: true,
  });
  await expect(create).toBeDisabled();
  await page
    .getByLabel("I understand this is synthetic demonstration data.", {
      exact: true,
    })
    .check();
  const actual = page.waitForResponse(
    (res) =>
      new URL(res.url()).pathname === "/api/v1/engine-runs/demo-dataset" &&
      res.request().method() === "POST",
  );
  await create.click();
  const response = await actual;
  expect(response.status()).toBe(200);
  const receipt = (await response.json()) as StructuredDemoReceipt;
  expect(receipt.research_submitted).toBe(false);
  expect(receipt.dataset.is_synthetic).toBe(true);
  expect(receipt.qualification).toBe(
    "synthetic_by_construction_not_live_or_pit_evidence",
  );
  await page
    .getByRole("button", { name: "Use returned demo preset", exact: true })
    .click();
  await expect(
    page.getByLabel("Retained dataset", { exact: true }),
  ).toHaveValue(receipt.dataset.id);
  await expect(
    page.getByLabel("Output window start (inclusive)", { exact: true }),
  ).toHaveValue(receipt.rd_preset.window.start);
  await expect(
    page.getByLabel("Output window end (exclusive)", { exact: true }),
  ).toHaveValue(receipt.rd_preset.window.end);
  await expect(page.locator('input[name="acknowledged"]')).not.toBeChecked();
  expect(calls).toEqual(["POST /api/v1/engine-runs/demo-dataset"]);
  await page.reload();
  await expect(
    page.getByRole("button", {
      name: "Create synthetic demo dataset",
      exact: true,
    }),
  ).toBeDisabled();
  expect(calls).toEqual(["POST /api/v1/engine-runs/demo-dataset"]);
  save(receipt);
});

test("normal search explicitly checks applies budget and submits one immutable request", async ({
  page,
  request,
}) => {
  const input = observations();
  expect(input.mode).toBe("submit");
  if (input.mode !== "submit")
    throw new Error("Submission observation required");
  const wanted = input.request as ProviderStructuredSearchRequest;
  const headers = authorization(await token(request));
  const catalogResponse = await request.get(
    api("/engine-runs/structured-search/snapshots?offset=0&limit=50"),
    { headers },
  );
  expect(catalogResponse.status()).toBe(200);
  const catalog = (await catalogResponse.json()) as StructuredSnapshotPage;
  const selected = catalog.items.find(
    (item) => item.snapshot_id === wanted.snapshot_id,
  );
  expect(selected).toBeDefined();
  expect(selected?.qualification).toBe(
    "released_historical_metadata_requires_fresh_preflight",
  );
  expect(z.uuid().safeParse(selected?.release_receipt_id).success).toBe(true);
  await signIn(page);
  const calls = mutations(page);
  await page.goto("/engine-runs/structured-search");
  await expect(
    page.getByRole("heading", {
      name: "Fixed-factor strategy search",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByLabel("Released execution snapshot", { exact: true }),
  ).toHaveValue("");
  await expect(
    page.getByLabel("Operator model profile", { exact: true }),
  ).toHaveValue("");
  await page
    .getByLabel("Released execution snapshot", { exact: true })
    .selectOption(wanted.snapshot_id);
  await page
    .getByLabel("Operator model profile", { exact: true })
    .selectOption(wanted.model_profile_id);
  const fields = [
    ["Factor eligibility threshold", wanted.seed_parameters.threshold],
    ["Maximum ranked instruments", wanted.seed_parameters.top_k],
    [
      "Maximum target weight per instrument",
      wanted.seed_parameters.maximum_weight,
    ],
    [
      "Maximum total target weight",
      wanted.seed_parameters.maximum_gross_weight,
    ],
    ["Execution window start (inclusive)", wanted.objective.window.start],
    ["Execution window end (exclusive)", wanted.objective.window.end],
    ["Initial cash", wanted.objective.initial_cash],
    [
      "Minimum closed trades (optional)",
      wanted.objective.minimum_closed_trades ?? "",
    ],
    ["Maximum model mutations", wanted.maximum_mutation_slots],
  ] as const;
  for (const [label, value] of fields)
    await page.getByLabel(label, { exact: true }).fill(String(value));
  for (const field of oeBudgetFields)
    await expect(page.getByLabel(field, { exact: true })).toHaveValue("0");
  const submit = page.getByRole("button", {
    name: "Submit exact checked OE request",
    exact: true,
  });
  await expect(submit).toBeDisabled();
  const waitPreflight = () =>
    page.waitForResponse(
      (res) =>
        new URL(res.url()).pathname ===
          "/api/v1/engine-runs/structured-search/preflight" &&
        res.request().method() === "POST",
    );
  const zeroResponse = waitPreflight();
  await page
    .getByRole("button", { name: "Check normal OE eligibility", exact: true })
    .click();
  const zeroHttp = await zeroResponse;
  expect(zeroHttp.status()).toBe(200);
  const zero = (await zeroHttp.json()) as ProviderStructuredPreflight;
  expect(zero.can_submit).toBe(false);
  expect(zero.required_budget).toEqual(wanted.budget);
  await expect(submit).toBeDisabled();
  await page
    .getByRole("button", {
      name: "Apply required budget to draft",
      exact: true,
    })
    .click();
  await expect(submit).toBeDisabled();
  for (const field of oeBudgetFields)
    await expect(page.getByLabel(field, { exact: true })).toHaveValue(
      String(wanted.budget[field]),
    );
  const freshResponse = waitPreflight();
  await page
    .getByRole("button", { name: "Check normal OE eligibility", exact: true })
    .click();
  const freshHttp = await freshResponse;
  expect(freshHttp.status()).toBe(200);
  const fresh = (await freshHttp.json()) as ProviderStructuredPreflight;
  expect(fresh.can_submit).toBe(true);
  expect(fresh.normalized_request).toEqual(wanted);
  await expect(submit).toBeEnabled();
  const observed = page.waitForResponse(
    (res) =>
      new URL(res.url()).pathname === "/api/v1/engine-runs/structured-search" &&
      res.request().method() === "POST",
  );
  await submit.click();
  const accepted = await observed;
  expect(accepted.status()).toBe(201);
  const receipt =
    (await accepted.json()) as ProviderStructuredSubmissionReceipt;
  const body = accepted.request().postDataJSON();
  const key = z.uuid().parse(accepted.request().headers()["idempotency-key"]);
  expect(body).toEqual({ ...wanted, preflight_sha256: fresh.preflight_sha256 });
  expect(receipt.normalized_request).toEqual(wanted);
  expect(receipt.preflight_sha256).toBe(fresh.preflight_sha256);
  await expect(
    page.getByRole("heading", {
      name: "Recorded submission receipt",
      exact: true,
    }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("link", { name: "Open owned search status", exact: true }),
  ).toHaveAttribute(
    "href",
    `/engine-runs/structured-search/runs/${receipt.run_id}`,
  );
  expect(calls).toEqual([
    "POST /api/v1/engine-runs/structured-search/preflight",
    "POST /api/v1/engine-runs/structured-search/preflight",
    "POST /api/v1/engine-runs/structured-search",
  ]);
  save({ receipt, idempotency_key: key, body });
});

test("actual normal run status reload preserves owner and release boundaries", async ({
  page,
  request,
  browser,
}) => {
  const input = observations();
  expect(input.mode).toBe("status");
  if (input.mode !== "status") throw new Error("Actual run ID required");
  const path = `/engine-runs/structured-search/runs/${input.run_id}`;
  const headers = authorization(await token(request));
  const response = await request.get(api(path), { headers });
  expect(response.status()).toBe(200);
  const actual = (await response.json()) as ProviderStructuredRunView;
  expect(actual.id).toBe(input.run_id);
  expect(actual.status).toBe("succeeded");
  expect(actual).not.toHaveProperty("orders");
  expect(actual).not.toHaveProperty("fills");
  await signIn(page);
  const calls = mutations(page);
  await page.goto(path);
  await expect(
    page.getByRole("heading", {
      name: "Actual operator search status",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByText(`Run ${input.run_id}:`, { exact: false }),
  ).toContainText("succeeded");
  await expect(
    page.getByText("Only owned run metadata is shown here.", { exact: false }),
  ).toBeVisible();
  await expect(page.getByRole("table")).toHaveCount(1);
  await expect(
    page.getByRole("button", { name: /release|submit|resume|cancel|pause/i }),
  ).toHaveCount(0);
  await page.reload();
  await expect(
    page.getByRole("heading", {
      name: "Actual operator search status",
      exact: true,
    }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Refresh actual OE status", exact: true })
    .click();
  expect(calls).toEqual([]);
  const otherHeaders = authorization(await token(request, true));
  const denied = await request.get(api(path), { headers: otherHeaders });
  expect(denied.status()).toBe(404);
  const foreignCatalog = await request.get(
    api("/engine-runs/structured-search/snapshots?offset=0&limit=50"),
    { headers: otherHeaders },
  );
  expect(foreignCatalog.status()).toBe(200);
  expect(
    ((await foreignCatalog.json()) as StructuredSnapshotPage).items.some(
      (item) =>
        item.snapshot_id === actual.submission.normalized_request.snapshot_id,
    ),
  ).toBe(false);
  const context = await browser.newContext({
    baseURL: z.url().parse(process.env.E2E_BASE_URL),
  });
  try {
    const otherPage = await context.newPage();
    await signIn(otherPage, true);
    const deniedRead = otherPage.waitForResponse(
      (res) => new URL(res.url()).pathname === `/api/v1${path}`,
    );
    await otherPage.goto(path);
    expect((await deniedRead).status()).toBe(404);
    await expect(
      otherPage.getByRole("heading", {
        name: "Actual operator search status",
        exact: true,
      }),
    ).toHaveCount(0);
  } finally {
    await context.close();
  }
  save({
    run_id: input.run_id,
    status: actual.status,
    other_owner_status: denied.status(),
  });
});

test("actual normal results require separate releases and preserve owner isolation", async ({
  page,
  request,
  browser,
}) => {
  const input = observations();
  if (input.mode !== "results") throw new Error("Result observation required");
  const headers = authorization(await token(request));
  const base = `/engine-runs/structured-search/runs/${input.run_id}`;
  const recordsResponse = await request.get(api(`${base}/executions`), {
    headers,
  });
  expect(recordsResponse.status()).toBe(200);
  const records = (await recordsResponse.json()) as NormalExecutionRecord[];
  expect(records.map((row) => row.execution_id)).toEqual(input.execution_ids);
  expect(records[1].parent_candidate_id).toBe(records[0].candidate_id);
  for (const id of input.execution_ids) {
    const withheld = await request.get(api(`${base}/executions/${id}`), {
      headers,
    });
    expect(withheld.status()).toBe(409);
  }
  await signIn(page);
  const calls = mutations(page);
  await page.goto(base);
  const inspect = () =>
    page
      .getByRole("button", {
        name: `Inspect execution ${input.execution_ids[1]}`,
        exact: true,
      })
      .click();
  const lineage = page.getByRole("region", {
    name: "Structured candidate version lineage",
    exact: true,
  });
  await expect(lineage).toBeVisible();
  await expect(lineage).toContainText("not native Python checkpoints");
  for (const record of records) {
    await expect(lineage).toContainText(record.candidate_id);
    await expect(lineage).toContainText(record.program_version_id);
  }
  await inspect();
  await expect(
    lineage.getByRole("button", {
      name: `Inspect execution ${input.execution_ids[1]}`,
      exact: true,
    }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(
    page.getByRole("button", { name: /^Prepare guidance from this released/ }),
  ).toHaveCount(0);
  const compare = page.getByRole("button", {
    name: "Compare released parent and child",
    exact: true,
  });
  await expect(compare).toBeDisabled();
  for (const [index, side] of ["parent", "child"].entries()) {
    const id = input.execution_ids[index];
    const release = page.getByRole("button", {
      name: `Release ${side} execution ${id}`,
      exact: true,
    });
    await expect(release).toBeDisabled();
    const acknowledgement = page.getByLabel(
      `I explicitly authorize ${side} factor and market result exposure.`,
      { exact: true },
    );
    await expect(acknowledgement).not.toBeChecked();
    await acknowledgement.check();
    const released = page.waitForResponse(
      (res) =>
        new URL(res.url()).pathname ===
          `/api/v1${base}/executions/${id}/release` &&
        res.request().method() === "POST",
    );
    await release.click();
    expect((await released).status()).toBe(200);
    const actualResponse = await request.get(api(`${base}/executions/${id}`), {
      headers,
    });
    expect(actualResponse.status()).toBe(200);
    const actual = (await actualResponse.json()) as NormalExecutionView;
    const panel = page.getByRole("region", {
      name: `${side === "parent" ? "Parent" : "Child"} released normal result`,
      exact: true,
    });
    await expect(panel).toBeVisible();
    await expect(
      panel.getByRole("heading", {
        name: "Observed native orders",
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      panel.getByRole("heading", {
        name: "Observed native fills",
        exact: true,
      }),
    ).toBeVisible();
    for (const order of actual.execution.orders)
      await expect(
        panel.getByRole("heading", {
          name: order.native_order_id,
          exact: true,
        }),
      ).toBeVisible();
    for (const fill of actual.execution.fills)
      await expect(
        panel.getByRole("heading", { name: fill.native_fill_id, exact: true }),
      ).toBeVisible();
    if (!actual.execution.orders.length)
      await expect(
        panel.getByText("No observed orders.", { exact: true }),
      ).toBeVisible();
    const prepare = panel.getByRole("button", {
      name: `Prepare guidance from this released ${side} result`,
      exact: true,
    });
    await prepare.click();
    const draft = panel.getByRole("region", {
      name: "Review observation-bound guidance",
      exact: true,
    });
    await expect(draft).toBeVisible();
    const context = JSON.parse((await draft.locator("pre").textContent())!);
    expect(context.kind).toBe("released_structured_oe_result");
    expect(context.refs).toMatchObject({
      candidate_id: actual.candidate_id,
      run_id: input.run_id,
      execution_id: actual.execution.execution_id,
      program_version_id: actual.execution.structured_strategy_version_id,
      snapshot_id: actual.execution.execution_snapshot_id,
      result_sha256: actual.execution.result_sha256,
      program_sha256: actual.execution.program_sha256,
      market_sha256: actual.execution.market_sha256,
    });
    await expect(
      draft.getByRole("button", {
        name: "Save observation-bound steer version",
        exact: true,
      }),
    ).toBeDisabled();
    await draft
      .getByRole("button", { name: "Close observation draft", exact: true })
      .click();
    await expect(draft).toHaveCount(0);
    // Closing must not make the same observation a one-shot-only action.
    await prepare.click();
    await expect(draft).toBeVisible();
    await draft
      .getByRole("button", { name: "Close observation draft", exact: true })
      .click();
    if (actual.execution.fills.length) {
      const fill = actual.execution.fills[0];
      await panel
        .getByLabel(
          `${side === "parent" ? "Parent" : "Child"} observed asset and session`,
          { exact: true },
        )
        .selectOption(fill.native_fill_id);
      await panel
        .getByRole("button", {
          name: `Prepare guidance from selected ${side} asset and session`,
          exact: true,
        })
        .click();
      await expect(draft).toBeVisible();
      const selectedContext = JSON.parse(
        (await draft.locator("pre").textContent())!,
      );
      expect(selectedContext.kind).toBe("released_structured_oe_fill");
      expect(selectedContext.refs).toMatchObject({
        ...context.refs,
        listing_id: fill.listing_id,
        native_fill_id: fill.native_fill_id,
      });
      expect(selectedContext.sessions).toEqual([fill.session_date]);
      await draft
        .getByRole("button", { name: "Close observation draft", exact: true })
        .click();
    } else {
      await expect(
        panel.getByText("No observed fills provide asset/session context", {
          exact: false,
        }),
      ).toBeVisible();
      await expect(
        panel.getByRole("button", { name: /Prepare guidance from selected/ }),
      ).toHaveCount(0);
    }
    if (index === 0) {
      expect(
        (
          await request.get(
            api(`${base}/executions/${input.execution_ids[1]}`),
            { headers },
          )
        ).status(),
      ).toBe(409);
      await expect(compare).toBeDisabled();
    }
  }
  const compared = page.waitForResponse(
    (res) => new URL(res.url()).pathname === `/api/v1${base}/comparison`,
  );
  await compare.click();
  const response = await compared;
  expect(response.status()).toBe(200);
  const pair = (await response.json()) as NormalPairComparison;
  expect([
    pair.before.execution.execution_id,
    pair.after.execution.execution_id,
  ]).toEqual(input.execution_ids);
  expect(pair.qualification).toBe("fixed_snapshot_comparison_not_causal_gain");
  await expect(page.getByText(/Score delta:/)).toBeVisible();
  const wantedCalls = input.execution_ids.map(
    (id) => `POST /api/v1${base}/executions/${id}/release`,
  );
  expect(calls).toEqual(wantedCalls);
  await page.reload();
  await inspect();
  await expect(
    page.getByRole("region", {
      name: "Child released normal result",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /^Release .* execution/ }),
  ).toHaveCount(0);
  expect(calls).toEqual(wantedCalls);
  const otherHeaders = authorization(await token(request, true));
  for (const id of input.execution_ids)
    expect(
      (
        await request.get(api(`${base}/executions/${id}`), {
          headers: otherHeaders,
        })
      ).status(),
    ).toBe(404);
  expect(
    (
      await request.get(
        api(
          `${base}/comparison?before=${input.execution_ids[0]}&after=${input.execution_ids[1]}`,
        ),
        { headers: otherHeaders },
      )
    ).status(),
  ).toBe(404);
  const context = await browser.newContext({
    baseURL: z.url().parse(process.env.E2E_BASE_URL),
  });
  try {
    const foreign = await context.newPage();
    await signIn(foreign, true);
    const denied = foreign.waitForResponse(
      (res) => new URL(res.url()).pathname === `/api/v1${base}`,
    );
    await foreign.goto(base);
    expect((await denied).status()).toBe(404);
    await expect(
      foreign.getByRole("heading", {
        name: "Observed native orders",
        exact: true,
      }),
    ).toHaveCount(0);
  } finally {
    await context.close();
  }
  save({
    run_id: input.run_id,
    execution_ids: input.execution_ids,
    explicit_release_count: 2,
    other_owner_status: 404,
  });
});

test("actual workflow normal notes retain exact released rows and restore boundaries", async ({
  page,
  request,
  browser,
}) => {
  const input = observations();
  if (input.mode !== "normal_notes" && input.mode !== "normal_notes_edit")
    throw new Error("Normal note observation required");
  expect(input.revisions).toHaveLength(
    input.mode === "normal_notes_edit" ? 2 : 4,
  );
  const headers = authorization(await token(request));
  const otherHeaders = authorization(await token(request, true));
  const base = `/engine-runs/structured-search/runs/${input.run_id}`;
  const metadata = await request.get(api(base), { headers });
  expect(metadata.status()).toBe(200);
  const actualMetadata = await metadata.json();
  expect(actualMetadata.schema_version).toBe(
    "mineagent.oe-structured-run-view.v2",
  );
  expect(actualMetadata.submission.origin).toBe("workflow_stage");
  const resultResponse = await request.get(
    api(`${base}/executions/${input.execution_id}`),
    { headers },
  );
  expect(resultResponse.status()).toBe(200);
  const result = (await resultResponse.json()) as NormalExecutionView;
  expect(result.execution.execution_id).toBe(input.execution_id);
  await signIn(page);
  const calls = mutations(page);
  // Use the actual deployment prefix from the loaded app, not a guessed root.
  const prefix =
    (await page
      .locator('meta[name="mineagent-subpath"]')
      .getAttribute("content")) ?? "";
  const href = (path: string) => `${prefix}${path}`;
  await page.goto(href(`${base}?execution_id=${input.execution_id}`));
  await expect(
    page.getByText(
      "Exact execution selected from the URL; result disclosure still requires its existing release.",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", {
      name: "Pin a normal structured OE observation",
      exact: true,
    }),
  ).toBeVisible();
  expect(calls).toEqual([]);
  const restored = page.locator("section.panel").filter({
    has: page.getByRole("heading", {
      name: "Restored exact normal OE note observation",
      exact: true,
    }),
  });
  for (const expected of input.revisions) {
    const response = await request.get(api(expected.path), { headers });
    expect(response.status()).toBe(200);
    const note = (await response.json()) as ResearchNoteRevision;
    expect(note.revision_sha256).toBe(expected.revision_sha256);
    expect(note.restore_href).toBe(expected.restore_href);
    expect(
      (
        await request.get(api(expected.path), { headers: otherHeaders })
      ).status(),
    ).toBe(404);
    await page.goto(href(expected.restore_href));
    await expect(restored).toBeVisible();
    await expect(restored).toContainText(input.execution_id);
    const query = new URL(expected.restore_href, "https://notes.invalid")
      .searchParams;
    if (query.get("kind") === "normal_oe_decision") {
      const seq = Number(query.get("decision_sequence"));
      const rows = result.execution.decisions.filter(
        (row) => row.event_seq === seq,
      );
      expect(rows).toHaveLength(1);
      expect(JSON.parse(await restored.locator("pre").innerText())).toEqual(
        rows[0],
      );
    } else {
      expect(query.get("kind")).toBe("normal_oe_fill");
      const rows = result.execution.fills.filter(
        (row) => row.native_fill_id === query.get("native_fill_id"),
      );
      expect(rows).toHaveLength(1);
      expect(JSON.parse(await restored.locator("pre").innerText())).toEqual(
        rows[0],
      );
    }
    expect(calls).toEqual([]);
  }
  const changed = new URL(
    input.revisions[0].restore_href,
    "https://notes.invalid",
  );
  const hash = changed.searchParams.get("row_sha256")!;
  changed.searchParams.set(
    "row_sha256",
    (hash[0] === "0" ? "1" : "0") + hash.slice(1),
  );
  await page.goto(href(changed.pathname + changed.search));
  await expect(
    page.getByText(
      "Exact note evidence unavailable: owner, original identity or row commitment changed.",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(restored).toHaveCount(0);
  await page.goto(
    href(`${base}?execution_id=00000000-0000-0000-0000-000000000000`),
  );
  await expect(
    page.getByText(
      "Exact requested execution is unavailable or ambiguous. No candidate was substituted.",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", {
      name: "Pin a normal structured OE observation",
      exact: true,
    }),
  ).toHaveCount(0);
  expect(
    (await request.get(api(base), { headers: otherHeaders })).status(),
  ).toBe(404);
  const context = await browser.newContext({
    baseURL: z.url().parse(process.env.E2E_BASE_URL),
  });
  try {
    const foreign = await context.newPage();
    await signIn(foreign, true);
    const foreignCalls = mutations(foreign);
    const denied = foreign.waitForResponse((res) =>
      new URL(res.url()).pathname.endsWith(`/api/v1${base}`),
    );
    await foreign.goto(href(input.revisions[0].restore_href));
    expect((await denied).status()).toBe(404);
    await expect(
      foreign.getByRole("heading", {
        name: "Restored exact normal OE note observation",
        exact: true,
      }),
    ).toHaveCount(0);
    expect(foreignCalls).toEqual([]);
  } finally {
    await context.close();
  }
  expect(calls).toEqual([]);
  const editorRevisions: Array<{
    note_id: string;
    revision: number;
    revision_sha256: string;
  }> = [];
  if (input.mode === "normal_notes_edit") {
    // This is the already executed workflow child, never a fabricated note row.
    expect(result.execution.fills.length).toBeGreaterThan(0);
    const fill = result.execution.fills[0];
    await page.goto(href(`${base}?execution_id=${input.execution_id}`));
    const composer = page.locator("section.panel").filter({
      has: page.getByRole("heading", {
        name: "Pin a normal structured OE observation",
        exact: true,
      }),
    });
    await expect(composer).toBeVisible();
    await composer
      .getByLabel("Normal OE note observation", { exact: true })
      .selectOption(`fill:${fill.native_fill_id}`);
    const captureResponse = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return (
        response.request().method() === "GET" &&
        url.pathname.endsWith(
          `/api/v1/research-note-evidence/normal-oe/${input.run_id}/${input.execution_id}`,
        ) &&
        url.searchParams.get("kind") === "normal_oe_fill" &&
        url.searchParams.get("native_fill_id") === fill.native_fill_id
      );
    });
    await composer
      .getByRole("button", {
        name: "Capture exact released note evidence",
        exact: true,
      })
      .click();
    const capturedResponse = await captureResponse;
    expect(capturedResponse.status()).toBe(200);
    const captured = await capturedResponse.json();
    expect(captured.qualification).toBe(
      "released_original_identity_not_note_write",
    );
    expect(captured.evidence.execution_id).toBe(input.execution_id);
    expect(captured.evidence.run_id).toBe(input.run_id);
    expect(captured.evidence.native_fill_id).toBe(fill.native_fill_id);
    expect(captured.evidence.listing_id).toBe(fill.listing_id);
    expect(captured.evidence.session_date).toBe(fill.session_date);
    await composer
      .getByLabel("Normal note title", { exact: true })
      .fill("Actual workflow fill observation from composer");
    await composer
      .getByLabel("Normal note body", { exact: true })
      .fill("First browser-authored observation; no execution authority.");
    const createdResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname.endsWith("/api/v1/research-notes"),
    );
    await composer
      .getByRole("button", {
        name: "Save exact normal note revision",
        exact: true,
      })
      .click();
    const created = await createdResponse;
    expect(created.status()).toBe(201);
    expect(created.request().headers()["idempotency-key"]).toBeTruthy();
    const first = (await created.json()) as ResearchNoteRevision;
    expect(first.revision).toBe(1);
    expect(first.content.title).toBe(
      "Actual workflow fill observation from composer",
    );
    expect(first.content.body).toBe(
      "First browser-authored observation; no execution authority.",
    );
    expect(first.content.evidence).toEqual([captured.evidence]);
    expect(first.restore_href).toBe(captured.href);
    await expect(composer.getByRole("status")).toContainText(
      `Saved note ${first.note_id} revision 1.`,
    );
    await composer
      .getByLabel("Normal note body", { exact: true })
      .fill("Second browser-authored observation; original fill unchanged.");
    const editedResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname.endsWith(
          `/api/v1/research-notes/${first.note_id}/revisions`,
        ),
    );
    await composer
      .getByRole("button", {
        name: "Save exact normal note revision",
        exact: true,
      })
      .click();
    const edited = await editedResponse;
    expect(edited.status()).toBe(201);
    const submitted = edited.request().postDataJSON();
    expect(submitted.base_revision).toBe(1);
    expect(submitted.base_revision_sha256).toBe(first.revision_sha256);
    expect(edited.request().headers()["idempotency-key"]).toBeTruthy();
    expect(edited.request().headers()["idempotency-key"]).not.toBe(
      created.request().headers()["idempotency-key"],
    );
    const second = (await edited.json()) as ResearchNoteRevision;
    expect(second.note_id).toBe(first.note_id);
    expect(second.revision).toBe(2);
    expect(second.content.body).toBe(
      "Second browser-authored observation; original fill unchanged.",
    );
    expect(second.content.evidence).toEqual(first.content.evidence);
    expect(second.restore_href).toBe(first.restore_href);
    await expect(composer.getByRole("status")).toContainText(
      `Saved note ${first.note_id} revision 2.`,
    );
    await page.reload();
    await expect(composer).toContainText(
      `Editing note ${first.note_id}, base revision 2.`,
    );
    await expect(
      composer.getByLabel("Normal note body", { exact: true }),
    ).toHaveValue(
      "Second browser-authored observation; original fill unchanged.",
    );
    await page.goto(href(second.restore_href!));
    await expect(restored).toBeVisible();
    expect(JSON.parse(await restored.locator("pre").innerText())).toEqual(fill);
    expect(calls).toEqual([
      `POST ${prefix}/api/v1/research-notes`,
      `POST ${prefix}/api/v1/research-notes/${first.note_id}/revisions`,
    ]);
    for (const revision of [first, second])
      editorRevisions.push({
        note_id: revision.note_id,
        revision: revision.revision,
        revision_sha256: revision.revision_sha256,
      });
  }
  save({
    run_id: input.run_id,
    execution_id: input.execution_id,
    editor_revisions: editorRevisions,
    restored_revisions: input.revisions.map((item) => item.revision_sha256),
    other_owner_status: 404,
    mutation_count: calls.length,
  });
});
