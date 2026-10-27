import { test, expect } from "@playwright/test";
import { account, api, authorization, token } from "../e2e/helpers";
import type { EngineRequest } from "../../src/lib/engine-runs-api";

test("actual configured entry creates one queued run and survives reload", async ({
  page,
  request,
}) => {
  const declaration = JSON.parse(
    process.env.E2E_RD_REQUEST_JSON || "null",
  ) as EngineRequest | null;
  if (
    !declaration ||
    declaration.dataset_version_id !== process.env.E2E_RD_DATASET_ID
  )
    throw new Error("Actual retained request declaration is required");
  const configuredModel =
    declaration.schema_version === "mineagent.rdagent-provider-submission.v1" ||
    declaration.schema_version ===
      "mineagent.rdagent-provider-seeded-submission.v1";
  if (
    !configuredModel &&
    declaration.schema_version !== "mineagent.rdagent-submission.v1"
  )
    throw new Error(
      "This browser flow requires an explicit supported request family",
    );
  const headers = authorization(await token(request));
  const before = await request.get(api("/engine-runs"), { headers });
  expect(before.status()).toBe(200);
  const beforeCount = (await before.json()).count;
  if (!("parent_factor_version_id" in declaration)) expect(beforeCount).toBe(0);
  const user = account();
  await page.goto("/login");
  await page.getByLabel("Email address").fill(user.email);
  await page.getByLabel("Password", { exact: true }).fill(user.password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Research overview" }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Engine history", exact: true }).click();
  await expect(
    page.getByText(
      configuredModel
        ? "available for an explicitly selected authorized model"
        : "Configured submission available",
      { exact: false },
    ),
  ).toBeVisible();
  await page
    .getByRole("link", { name: "Check RD-Agent dataset eligibility" })
    .click();
  if (configuredModel && "model_profile_id" in declaration) {
    await page
      .getByLabel("Generation mode", { exact: true })
      .selectOption("configured");
    await page
      .getByLabel("Configured model profile", { exact: true })
      .selectOption(declaration.model_profile_id);
    if ("parent_factor_version_id" in declaration)
      await page
        .getByLabel(
          "Exact parent factor version (optional; leave empty for a new root)",
        )
        .fill(declaration.parent_factor_version_id);
    for (const version of declaration.selected_steer_version_ids ?? []) {
      if (!/^[a-f0-9-]{36}$/i.test(version))
        throw new Error("Exact steer version UUID required");
      await page
        .locator(`input[name="selected-steer-version"][value="${version}"]`)
        .check();
    }
  }
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
  await page
    .getByLabel("Iterations", { exact: true })
    .fill(String(declaration.max_iterations));
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
  const checked = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname === "/api/v1/engine-runs/preflight" &&
      r.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Check eligibility", exact: true })
    .click();
  const preflight = await checked;
  expect(preflight.status()).toBe(200);
  const result = await preflight.json();
  expect(result.can_submit).toBe(true);
  expect(result.request_sha256).toBe(process.env.E2E_RD_REQUEST_SHA256);
  if (configuredModel && "selected_steer_version_ids" in declaration) {
    expect(
      result.steer_snapshot.versions.map(
        (version: { version_id: string }) => version.version_id,
      ),
    ).toEqual(declaration.selected_steer_version_ids ?? []);
    expect(result.model_binding_sha256).toMatch(/^[a-f0-9]{64}$/);
    await expect(
      page.getByText("Exact user-provided steer snapshot (pinned)", {
        exact: true,
      }),
    ).toBeVisible();
  }
  const created = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname === "/api/v1/engine-runs" &&
      r.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Create RD-Agent run", exact: true })
    .click();
  const submitted = await created;
  expect(submitted.status()).toBe(201);
  const receipt = await submitted.json();
  expect(receipt.accepted).toBe(true);
  expect(receipt.status).toBe("queued");
  expect(receipt.request_sha256).toBe(result.request_sha256);
  expect(receipt.preflight_sha256).toBe(result.preflight_sha256);
  if (configuredModel)
    expect(receipt.model_binding_sha256).toBe(result.model_binding_sha256);
  if ("parent_factor_version_id" in declaration)
    expect(receipt.parent_factor_version_id).toBe(
      declaration.parent_factor_version_id,
    );
  expect(submitted.request().headers()["idempotency-key"]).toMatch(
    /^[a-f0-9-]{36}$/i,
  );
  await expect(page).toHaveURL(
    (url) => url.pathname === `/engine-runs/${receipt.run_id}`,
  );
  await expect(
    page.getByRole("heading", { name: declaration.task.name, exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("queued · Generation", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Cancel RD-Agent run", exact: true }),
  ).toBeVisible();
  const inputsResponse = await request.get(
    api(`/engine-runs/${receipt.run_id}/inputs`),
    { headers },
  );
  expect(inputsResponse.status()).toBe(200);
  const inputs = await inputsResponse.json();
  expect(inputs.items).toHaveLength(1);
  expect(inputs.items[0].origin).toBe("user_provided");
  expect(inputs.items[0].default_pinned).toBe(true);
  expect(inputs.items[0].content_sha256).toBe(receipt.request_sha256);
  expect(inputs.items[0].submission).toEqual(declaration);
  if (configuredModel) {
    expect(inputs.items[0].steer_snapshot).toEqual(result.steer_snapshot);
    expect(inputs.items[0].steer_snapshot_sha256).toBe(
      result.steer_snapshot_sha256,
    );
    expect(inputs.items[0].model_binding_sha256).toBe(
      result.model_binding_sha256,
    );
    expect(inputs.items[0].steer_evidence).toBe(
      "accepted_input_snapshot_not_execution_or_compliance",
    );
    const frozen = page.getByRole("region", {
      name: "Frozen submission steer inputs",
    });
    await expect(frozen).toBeVisible();
    for (const version of result.steer_snapshot.versions)
      await expect(
        frozen.getByText(version.text, { exact: true }),
      ).toBeVisible();
  }

  await expect(
    page.getByLabel("Pinned user input", { exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: declaration.task.name, exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: "All engine history" }).click();
  await page
    .getByRole("link", { name: declaration.task.name, exact: true })
    .click();
  await expect(
    page.getByText("No generation records have been persisted."),
  ).toBeVisible();
  await expect(
    page.getByLabel("Pinned user input", { exact: true }),
  ).toBeVisible();
  const after = await request.get(api("/engine-runs"), { headers });
  expect((await after.json()).count).toBe(beforeCount + 1);
  // Real worker execution happens later in the Python gate, never inside this UI fixture.
});
