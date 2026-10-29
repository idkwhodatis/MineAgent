import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import {
  canRequestOECancel,
  OECancelControl,
  exactOERun,
  oeRunOptions,
  oeRunPollInterval,
  OEProviderRunMetadata,
} from "../../src/features/oe-provider-run";
import type {
  ProviderStructuredRunView,
  WorkflowStructuredRunView,
} from "../../src/client/types.gen";
const owner = "owner",
  run = "run";
const budget = {
  calls: 4,
  tokens: 1000,
  mutation_slots: 1,
  evaluator_executions: 2,
  wall_milliseconds: 10000,
  storage_bytes: 10000,
  process_slots: 4,
  cost_microunits: 100,
};
const zero = {
  calls: 0,
  tokens: 0,
  mutation_slots: 0,
  evaluator_executions: 0,
  wall_milliseconds: 0,
  storage_bytes: 0,
  process_slots: 0,
  cost_microunits: 0,
};
const value: ProviderStructuredRunView = {
  schema_version: "mineagent.oe-structured-run-view.v1",
  id: run,
  purpose: "strategy_search",
  engine: "openevolve",
  status: "running",
  generation: 1,
  active_attempt_id: "actual-attempt",
  created_at: "2026-10-26T22:00:00Z",
  updated_at: "2026-10-26T22:01:00Z",
  submission: {
    schema_version: "mineagent.oe-structured-provider-submission-receipt.v1",
    id: "receipt",
    run_id: run,
    origin: "authenticated_user_submission",
    origin_user_id: owner,
    request_sha256: "a".repeat(64),
    preflight_sha256: "b".repeat(64),
    required_process_units: 4,
    normalized_request: {
      snapshot_id: "snapshot",
      model_profile_id: "model",
      model_binding_sha256: "c".repeat(64),
      seed_parameters: {
        threshold: 0,
        top_k: 1,
        maximum_weight: 1,
        maximum_gross_weight: 1,
      },
      objective: {
        window: { start: "2024-03-13", end: "2024-03-15" },
        initial_cash: 100000,
      },
      budget,
      maximum_mutation_slots: 1,
    },
  },
  factor_version_id: "factor-version",
  dataset_version_id: "dataset",
  model_binding: {
    profile_id: "model",
    profile_sha256: "d".repeat(64),
    model: "chat",
    max_output_tokens: 100,
    request_deadline_ms: 1000,
  },
  budget_limits: budget,
  budget_used: zero,
  budget_reserved: budget,
  declared_process_units: 4,
};
it("normal read never accepts wrong owner/run or legacy engine family", () => {
  expect(exactOERun(value, owner, run)).toBe(value);
  expect(() => exactOERun(value, "another", run)).toThrow();
  expect(() => exactOERun(value, owner, "another")).toThrow();
  expect(() =>
    exactOERun(
      {
        ...value,
        purpose: "factor_research",
      } as unknown as ProviderStructuredRunView,
      owner,
      run,
    ),
  ).toThrow();
  expect(oeRunOptions(owner, run).queryKey).not.toEqual(
    oeRunOptions("another", run).queryKey,
  );
});
it("shows actual status/accounting without pretending controls or unreleased results exist", () => {
  const html = renderToStaticMarkup(
    createElement(OEProviderRunMetadata, { value }),
  );
  expect(html).toContain("running");
  expect(html).toContain("actual-attempt");
  expect(html).toContain("not a provider invoice");
  expect(html).toContain("Reserved");
  expect(html).toContain("Pause/resume are not supported");
  expect(html).toContain("not proof that those processes ran or stopped");
  expect(html).toContain("require separate explicit result release below");
  expect(html).not.toContain("<button");
});
it("polls only unresolved status and stops on terminal or failed owner reads", () => {
  expect(oeRunPollInterval("running")).toBe(5000);
  expect(oeRunPollInterval("queued")).toBe(5000);
  for (const status of ["succeeded", "failed", "cancelled", "interrupted"])
    expect(oeRunPollInterval(status)).toBe(false);
  expect(oeRunPollInterval("running", new Error("not_found"))).toBe(false);
});

it("cancellation uses actual active actions and never claims stop from a pending request", () => {
  expect(canRequestOECancel(value)).toBe(false);
  const enabled = {
    ...value,
    actions: {
      cancel: true,
      pause: false as const,
      resume: false as const,
      reason_code: "structured_pause_resume_not_supported" as const,
    },
  };
  expect(canRequestOECancel(enabled)).toBe(true);
  for (const status of [
    "succeeded",
    "failed",
    "cancelled",
    "interrupted",
    "cancel_requested",
  ] as const)
    expect(canRequestOECancel({ ...enabled, status })).toBe(false);
  const html = renderToStaticMarkup(
    createElement(OECancelControl, {
      value: { ...enabled, status: "cancel_requested" },
      refresh: () => {},
    }),
  );
  expect(html).toContain("waiting for actual containment and settlement");
  expect(html).toContain("No refund or positive stop is inferred");
  expect(html).not.toContain("<button");
});

it("workflow metadata has a distinct version and never becomes a user receipt or child cancel grant", () => {
  const id = "11111111-1111-4111-8111-111111111111";
  const owned = "22222222-2222-4222-8222-222222222222";
  const workflow: WorkflowStructuredRunView = {
    ...value,
    id,
    schema_version: "mineagent.oe-structured-run-view.v2",
    submission: {
      schema_version: "mineagent.oe-workflow-run-origin.v1",
      id,
      run_id: id,
      owner_id: owned,
      origin: "workflow_stage",
      workflow_id: id,
      stage_id: id,
      input_revision_id: id,
      snapshot_id: id,
      request_sha256: "a".repeat(64),
      preflight_sha256: "b".repeat(64),
      required_process_units: 4,
    },
  };
  expect(exactOERun(workflow, owned, id)).toBe(workflow);
  expect(canRequestOECancel({ ...workflow, actions: { cancel: true } })).toBe(
    false,
  );
  expect(() => exactOERun(workflow, owner, id)).toThrow();
  for (const changed of [
    { ...workflow, schema_version: value.schema_version },
    { ...workflow, submission: value.submission },
    {
      ...workflow,
      submission: {
        ...workflow.submission,
        origin: "authenticated_user_submission",
      },
    },
    {
      ...workflow,
      submission: { ...workflow.submission, stage_id: "invalid" },
    },
    {
      ...workflow,
      submission: { ...workflow.submission, origin_user_id: owned },
    },
  ])
    expect(() =>
      exactOERun(changed as WorkflowStructuredRunView, owned, id),
    ).toThrow();
  const html = renderToStaticMarkup(
    createElement(OEProviderRunMetadata, { value: workflow }),
  );
  expect(html).toContain("not a direct user submission");
  expect(html).toContain(`/research-workflows/${id}`);
  expect(html).toContain("must actually stop and settle first");
});
