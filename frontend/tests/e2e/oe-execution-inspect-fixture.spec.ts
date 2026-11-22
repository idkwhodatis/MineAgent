import { expect, test } from "@playwright/test";
import type {
  NormalExecutionRecord,
  WorkflowStructuredRunView,
} from "../../src/client/types.gen";
import { api, authorization, signIn, token } from "./helpers";

// Synthetic read-only OpenEvolve API responses; real application navigation
// and authentication. This does not exercise native RD/OE or prove release.
test("Inspect execution navigation and history keep mocked OE results withheld", async ({
  page,
  request,
}) => {
  const uid = (suffix: number) =>
    `00000000-0000-4000-8000-${String(suffix).padStart(12, "0")}`;
  const runId = uid(410);
  const snapshotId = uid(411);
  const sourceRunId = uid(412);
  const parentId = uid(420);
  const childId = uid(421);
  const parentExecution = uid(430);
  const childExecution = uid(431);
  const hash = "a".repeat(64);
  const fixedTime = "2026-01-01T00:00:00Z";
  const me = await request.get(api("/users/me"), {
    headers: authorization(await token(request)),
  });
  expect(me.ok()).toBe(true);
  const { id: ownerId } = await me.json();
  const path = `/engine-runs/structured-search/runs/${runId}`;
  const endpoint = `/api/v1${path}`;
  const records: NormalExecutionRecord[] = [
    {
      candidate_id: parentId,
      execution_id: parentExecution,
      ordinal: 0,
      origin: "workflow_seed",
      parent_candidate_id: null,
      program_version_id: uid(440),
      qualification: "identity_metadata_not_result_release",
      snapshot_id: snapshotId,
      source_run_id: sourceRunId,
    },
    {
      candidate_id: childId,
      execution_id: childExecution,
      ordinal: 1,
      origin: "model_proposal",
      parent_candidate_id: parentId,
      program_version_id: uid(441),
      qualification: "identity_metadata_not_result_release",
      snapshot_id: snapshotId,
      source_run_id: sourceRunId,
    },
  ];
  const run: WorkflowStructuredRunView = {
    id: runId,
    status: "succeeded",
    engine: "openevolve",
    purpose: "strategy_search",
    schema_version: "mineagent.oe-structured-run-view.v2",
    generation: 1,
    active_attempt_id: null,
    data_qualification: "synthetic_by_construction",
    dataset_version_id: uid(450),
    factor_version_id: uid(451),
    declared_process_units: 4,
    budget_limits: {},
    budget_reserved: {},
    budget_used: {},
    created_at: fixedTime,
    updated_at: fixedTime,
    model_binding: {
      profile_id: "inspect-fixture",
      profile_sha256: hash,
      model: "synthetic inspect fixture",
      max_output_tokens: 1,
      request_deadline_ms: 1000,
      synthetic: true,
    },
    submission: {
      schema_version: "mineagent.oe-workflow-run-origin.v1",
      id: uid(460),
      run_id: runId,
      owner_id: ownerId,
      origin: "workflow_stage",
      workflow_id: uid(461),
      stage_id: uid(462),
      input_revision_id: uid(463),
      snapshot_id: snapshotId,
      request_sha256: hash,
      preflight_sha256: hash,
      required_process_units: 4,
    },
  };
  await signIn(page);
  // Fail closed on writes rather than giving a simulated success receipt.
  const writes: string[] = [];
  const unexpectedReads: string[] = [];
  const reads = new Set<string>();
  await page.route(
    "**/api/v1/engine-runs/structured-search/**",
    async (route) => {
      const target = new URL(route.request().url());
      const method = route.request().method();
      if (method !== "GET") {
        writes.push(`${method} ${target.pathname}`);
        return route.abort();
      }
      const prefixAt = target.pathname.indexOf(endpoint);
      const relative = target.pathname.slice(prefixAt + endpoint.length);
      if (prefixAt >= 0 && !target.search) {
        if (!relative) return route.fulfill({ json: run });
        if (relative === "/executions") return route.fulfill({ json: records });
        if (
          relative === `/executions/${parentExecution}` ||
          relative === `/executions/${childExecution}`
        ) {
          reads.add(relative);
          return route.fulfill({
            status: 409,
            json: {
              code: "engine_result_release_required",
              message: "Explicit historical result release is required",
            },
          });
        }
      }
      unexpectedReads.push(target.pathname);
      return route.abort();
    },
  );
  const prefix =
    (await page
      .locator('meta[name="mineagent-subpath"]')
      .getAttribute("content")) ?? "";
  const href = (id: string) => `${prefix}${path}?execution_id=${id}`;
  const inspect = (id: string) =>
    page.getByRole("button", { name: `Inspect execution ${id}`, exact: true });
  const selected = async (id: string) => {
    await expect(page).toHaveURL(href(id));
    await expect(inspect(id)).toHaveAttribute("aria-pressed", "true");
    await expect(
      inspect(id === childExecution ? parentExecution : childExecution),
    ).toHaveAttribute("aria-pressed", "false");
  };

  await page.goto(href(childExecution));
  await selected(childExecution);
  const childConsent = page.getByLabel(
    "I explicitly authorize child factor and market result exposure.",
    { exact: true },
  );
  await expect(
    page.getByRole("button", {
      name: "Compare released parent and child",
      exact: true,
    }),
  ).toBeDisabled();
  await childConsent.check();
  await inspect(parentExecution).click();
  await selected(parentExecution);
  await expect(
    page.getByRole("button", {
      name: `Release selected execution ${parentExecution}`,
      exact: true,
    }),
  ).toBeDisabled();
  await page.goBack();
  await selected(childExecution);
  await expect(childConsent).not.toBeChecked();
  await page.goForward();
  await selected(parentExecution);
  await page.reload();
  await selected(parentExecution);
  expect(reads).toEqual(
    new Set([
      `/executions/${parentExecution}`,
      `/executions/${childExecution}`,
    ]),
  );
  expect(unexpectedReads).toEqual([]);
  expect(writes).toEqual([]);
});
