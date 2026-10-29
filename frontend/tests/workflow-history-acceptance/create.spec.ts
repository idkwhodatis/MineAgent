import { test, expect } from "@playwright/test";
import { writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { signIn } from "../e2e/helpers";
import type {
  WorkflowRequest,
  StructuredWorkflowRequest,
} from "../../src/workflow-contracts/types.gen";

test("actual workflow entry creates exactly one retained request", async ({
  page,
}) => {
  const expected = JSON.parse(
    process.env.E2E_WORKFLOW_CREATE_REQUEST ?? "null",
  ) as WorkflowRequest | StructuredWorkflowRequest;
  if (
    ![
      "mineagent.workflow-public-request.v1",
      "mineagent.workflow-public-request.v2",
    ].includes(expected?.schema_version ?? "") ||
    expected.research.schema_version !==
      "mineagent.rdagent-provider-submission.v1"
  )
    throw new Error("Exact public workflow fixture request required");
  if (
    !Array.isArray(expected.refs) ||
    expected.mode === undefined ||
    expected.maximum_iterations === undefined ||
    typeof expected.allow_workflow_development_feedback_release !== "boolean"
  )
    throw new Error("Explicit finite workflow policy required");
  const structured =
    expected.schema_version === "mineagent.workflow-public-request.v2"
      ? expected.structured
      : null;
  const submitPath = structured
    ? "/api/v1/research-workflows/structured"
    : "/api/v1/research-workflows";
  const declaration = expected.research;
  const raw = process.env.E2E_WORKFLOW_RAW ?? "";
  const output = process.env.E2E_WORKFLOW_CREATE_OUTPUT;
  if (!output) throw new Error("Private creation receipt path required");
  let enginePosts = 0;
  page.on("request", (r) => {
    if (
      r.method() === "POST" &&
      new URL(r.url()).pathname === "/api/v1/engine-runs"
    )
      enginePosts++;
  });
  await signIn(page);
  await page.goto("/research-workflows");
  const entry = page.getByRole("region", {
    name: "Finite research workflow entry",
    exact: true,
  });
  await expect(entry).toBeVisible();
  await expect(page.getByLabel("Generation mode", { exact: true })).toHaveValue(
    "configured",
  );
  await page
    .getByLabel("Configured model profile", { exact: true })
    .selectOption(declaration.model_profile_id);
  await page
    .getByLabel("Retained dataset", { exact: true })
    .selectOption(declaration.dataset_version_id);
  await page
    .getByLabel("Output window start (inclusive)")
    .fill(declaration.window.start);
  await page
    .getByLabel("Output window end (exclusive)")
    .fill(declaration.window.end);
  for (const field of ["open", "high", "low", "close", "volume"] as const)
    await page
      .getByLabel(field, { exact: true })
      .setChecked(declaration.input_fields.includes(field));
  await page
    .getByLabel("Prior lookback sessions")
    .fill(String(declaration.lookback_sessions));
  await page.getByLabel("Lag sessions").fill(String(declaration.lag_sessions));
  await expect(page.getByLabel("Iterations", { exact: true })).toHaveValue("1");
  await page.getByLabel("Task identifier").fill(declaration.task.name);
  await page.getByLabel("Task description").fill(declaration.task.description);
  await page
    .getByLabel("Factor formulation")
    .fill(declaration.task.formulation);
  await page
    .getByLabel("Factor kind")
    .selectOption(declaration.task.factor_kind);
  await page
    .getByLabel("Output unit identifier")
    .fill(declaration.task.output_unit);
  await page
    .getByLabel("Interpretation", { exact: true })
    .selectOption(declaration.task.interpretation);
  await page
    .getByLabel("Label horizon sessions")
    .fill(String(declaration.objective.label_horizon_sessions));
  await page
    .getByLabel("Objective metric")
    .selectOption(declaration.objective.metric);
  await page
    .getByLabel("Threshold direction")
    .selectOption(declaration.objective.direction);
  await page
    .getByLabel("Threshold", { exact: true })
    .fill(String(declaration.objective.threshold));
  await page
    .getByLabel("Minimum defined days")
    .fill(String(declaration.objective.minimum_defined_days));
  await page
    .getByLabel("Minimum pairs")
    .fill(String(declaration.objective.minimum_pairs));
  await page.getByLabel(/I declare daily session-close availability/).check();

  const researchChecked = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname === "/api/v1/engine-runs/preflight" &&
      r.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Check eligibility", exact: true })
    .click();
  const researchResponse = await researchChecked;
  expect(researchResponse.status()).toBe(200);
  const researchResult = await researchResponse.json();
  expect(researchResult.can_submit).toBe(true);
  expect(researchResult.normalized_request).toEqual(declaration);
  await page
    .getByRole("button", {
      name: "Use this declaration for workflow preflight — no run created",
      exact: true,
    })
    .click();
  for (const ref of expected.refs) {
    if (
      ref.origin !== "manual_user_submission" ||
      !/^[a-f0-9-]{36}$/i.test(ref.version_id)
    )
      throw new Error("Actual manual fixture reference required");
    await entry
      .locator(
        `input[name="workflow-manual-version"][value="${ref.version_id}"]`,
      )
      .check();
  }
  let rawReceipt: unknown = null;
  if (raw) {
    await entry.getByLabel("Raw external JSON data", { exact: true }).fill(raw);
    const retained = page.waitForResponse(
      (r) =>
        new URL(r.url()).pathname ===
          "/api/v1/research-inputs/external-objects" &&
        r.request().method() === "POST",
    );
    await entry
      .getByRole("button", { name: "Retain raw input", exact: true })
      .click();
    const response = await retained;
    expect(response.status()).toBe(201);
    rawReceipt = await response.json();
    expect(rawReceipt).toMatchObject({
      sha256: createHash("sha256").update(raw).digest("hex"),
      size_bytes: Buffer.byteLength(raw),
      qualification: "raw_retained_not_normalized",
      workflow_created: false,
    });
  }
  if (structured) {
    await entry
      .getByLabel(
        "Add one terminal operator-backed structured OE stage (opt-in)",
        { exact: true },
      )
      .check();
    const oe = entry.getByRole("region", {
      name: "Terminal structured OE declaration",
      exact: true,
    });
    await expect(
      oe.getByText(/waits for explicit snapshot and analysis release/),
    ).toBeVisible();
    await expect(
      oe.getByLabel("Released execution snapshot", { exact: true }),
    ).toHaveCount(0);
    await oe
      .getByLabel("Operator model profile", { exact: true })
      .selectOption(structured.model_profile_id);
    for (const [key, label] of [
      ["threshold", "Factor eligibility threshold"],
      ["top_k", "Maximum ranked instruments"],
      ["maximum_weight", "Maximum target weight per instrument"],
      ["maximum_gross_weight", "Maximum total target weight"],
    ] as const)
      await oe
        .getByLabel(label, { exact: true })
        .fill(String(structured.seed_parameters[key]));
    await oe
      .getByLabel("Execution window start (inclusive)")
      .fill(structured.objective.window.start);
    await oe
      .getByLabel("Execution window end (exclusive)")
      .fill(structured.objective.window.end);
    await oe
      .getByLabel("Initial cash", { exact: true })
      .fill(String(structured.objective.initial_cash));
    await oe
      .getByLabel("Minimum closed trades (optional)")
      .fill(
        structured.objective.minimum_closed_trades == null
          ? ""
          : String(structured.objective.minimum_closed_trades),
      );
    await oe
      .getByLabel("Maximum model mutations", { exact: true })
      .fill(String(structured.maximum_mutation_slots));
    for (const [key, value] of Object.entries(structured.budget))
      await oe.getByLabel(key, { exact: true }).fill(String(value));
  }
  await entry.getByLabel("Mode", { exact: true }).selectOption(expected.mode);
  await entry
    .getByLabel("Maximum iterations", { exact: true })
    .fill(String(expected.maximum_iterations));
  for (const [key, value] of Object.entries(expected.budget_ceiling))
    await entry
      .getByLabel(`Workflow budget ${key}`, { exact: true })
      .fill(String(value));
  await entry
    .getByLabel(/Allow this workflow’s eligible owned development outputs/)
    .setChecked(expected.allow_workflow_development_feedback_release);
  if (structured) {
    await expect(
      entry.getByRole("button", {
        name: "Check complete workflow",
        exact: true,
      }),
    ).toBeDisabled();
    await entry
      .getByLabel(/I explicitly set the total workflow budget/)
      .check();
  }
  const checked = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname.endsWith(`${submitPath}/preflight`) &&
      r.request().method() === "POST",
  );
  await entry
    .getByRole("button", { name: "Check complete workflow", exact: true })
    .click();
  expect((await checked).status()).toBe(200);
  const created = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname.endsWith(submitPath) &&
      r.request().method() === "POST",
  );
  await entry
    .getByRole("button", { name: "Create exact workflow request", exact: true })
    .click();
  const response = await created;
  expect(response.status()).toBe(201);
  const envelope = response.request().postDataJSON();
  const receipt = await response.json();
  expect(envelope.request).toEqual({
    ...expected,
    external_definition: rawReceipt
      ? {
          schema_version: "mineagent.external-normalization-stage.v1",
          raw_object_id: (rawReceipt as { id: string }).id,
          raw_sha256: (rawReceipt as { sha256: string }).sha256,
          mapping: {
            schema_version: "mineagent.external-feed-mapping.v1",
            items_path: ["items"],
            text_path: ["text"],
            title_path: ["title"],
            kind: "direction",
          },
        }
      : null,
  });
  expect(enginePosts).toBe(0);
  const encoded = JSON.stringify({
    envelope,
    receipt,
    raw_receipt: rawReceipt,
  });
  expect(Buffer.byteLength(encoded)).toBeLessThanOrEqual(131072);
  writeFileSync(output, encoded, { flag: "wx", mode: 0o600 });
});
