import { APP_SUBPATH } from "../../src/lib/deployment";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { WorkflowEntry } from "../../src/features/workflow-entry";
import { WorkflowHistoryView } from "../../src/features/workflow-history";
import {
  loadWorkflowEnvelope,
  saveWorkflowEnvelope,
  submitWorkflowEnvelope,
  type WorkflowEnvelope,
} from "../../src/lib/research-workflows";
import type {
  WorkflowView,
  WorkflowStageHistory,
} from "../../src/workflow-contracts/types.gen";
vi.mock("../../src/features/engine-preflight", () => ({
  EngineEntryForm: () =>
    createElement(
      "div",
      null,
      "Actual declaration component supplied at integration",
    ),
}));
vi.mock("../../src/lib/session", () => ({
  readSession: () => ({
    accessToken: "test-only",
    expiresAt: Date.now() + 10000,
  }),
  clearSession: vi.fn(),
}));
const owner = "30000000-0000-4000-8000-000000000001";
const key = "30000000-0000-4000-8000-000000000002";
const workflow = "30000000-0000-4000-8000-000000000003";
const envelope: WorkflowEnvelope = {
  owner,
  key,
  body_json: JSON.stringify({
    schema_version: "mineagent.workflow-public-submit.v1",
    idempotency_key: key,
    preflight_sha256: "a".repeat(64),
    request: { schema_version: "mineagent.workflow-public-request.v1" },
  }),
};
beforeEach(() => {
  const map = new Map<string, string>();
  vi.stubGlobal("sessionStorage", {
    getItem: (key: string) => map.get(key) ?? null,
    setItem: (key: string, value: string) => map.set(key, value),
    removeItem: (key: string) => map.delete(key),
  });
});
afterEach(() => vi.unstubAllGlobals());
it("retains the exact owner/key/body across reload and blocks replacement", () => {
  saveWorkflowEnvelope(envelope);
  expect(loadWorkflowEnvelope(owner)).toEqual({ state: "pending", envelope });
  expect(loadWorkflowEnvelope(workflow)).toEqual({ state: "empty" });
  expect(() => saveWorkflowEnvelope({ ...envelope, key: workflow })).toThrow(
    "unresolved",
  );
});
it("network uncertainty never clears or replaces the original request", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ id: owner })))
    .mockRejectedValueOnce(new Error("network uncertain"));
  vi.stubGlobal("fetch", fetcher);
  await expect(submitWorkflowEnvelope(envelope)).rejects.toThrow(
    "network uncertain",
  );
  expect(loadWorkflowEnvelope(owner)).toEqual({ state: "pending", envelope });
  expect(fetcher.mock.calls[1][1].body).toBe(envelope.body_json);
});
it("foreign authenticated owner cannot submit the saved request", async () => {
  const fetcher = vi.fn(
    async () => new Response(JSON.stringify({ id: workflow })),
  );
  vi.stubGlobal("fetch", fetcher);
  await expect(submitWorkflowEnvelope(envelope)).rejects.toThrow(
    "owner changed",
  );
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(loadWorkflowEnvelope(owner).state).toBe("pending");
});
it("accepted original receipt clears retry only, not claims current child execution", async () => {
  const receipt = {
    schema_version: "mineagent.workflow-created.v1",
    workflow_id: workflow,
    input_revision_id: key,
    request_sha256: "b".repeat(64),
    revision: 1,
    status: "blocked",
    current_stage_id: null,
  };
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: owner })))
      .mockResolvedValueOnce(new Response(JSON.stringify(receipt))),
  );
  expect(await submitWorkflowEnvelope(envelope)).toEqual(receipt);
  expect(loadWorkflowEnvelope(owner)).toEqual({ state: "empty" });
});
it("normal entry stays closed by default, manual first and feedback opt-in unchecked", () => {
  const html = renderToStaticMarkup(
    createElement(
      QueryClientProvider,
      { client: new QueryClient() },
      createElement(WorkflowEntry, {
        ownerId: owner,
        reason: "schema unavailable",
        onCreated: vi.fn(),
      }),
    ),
  );
  expect(html).toContain("Workflow creation unavailable");
  expect(html.indexOf("User-provided manual guidance")).toBeLessThan(
    html.indexOf("Agent-generated proposals"),
  );
  expect(html).toContain("not global or retroactive");
  expect(html).not.toContain('checked=""');
  expect(html).toContain("<fieldset disabled");
  expect(html).toContain('<label for="workflow-mode">Mode</label>');
  expect(html).toContain('<select id="workflow-mode"');
});
const root: WorkflowView = {
  schema_version: "mineagent.workflow-view.v1",
  workflow_id: workflow,
  revision: 2,
  mode: "continuous",
  status: "blocked",
  selected_input_revision_id: owner,
  current_stage: null,
  latest_checkpoint_id: null,
  blocked_reason: "child_adapter_unavailable",
  controls: {
    pause_after_stage: false,
    cancel: false,
    resume: false,
    reason_code: "closed",
  },
  qualification: "stage_metadata_not_result_release_or_process_authority",
};
it("missing current stage never becomes a fabricated running child", () => {
  const html = renderToStaticMarkup(
    createElement(WorkflowHistoryView, { workflow: root }),
  );
  expect(html).toContain(
    "No current stage recorded; no child launch is inferred",
  );
  expect(html).toContain("child_adapter_unavailable");
});
it("future selection differs honestly from old frozen stage and uncertain model request", () => {
  const stage: WorkflowStageHistory = {
    stage_id: key,
    ordinal: 1,
    kind: "external_normalization",
    input_revision_id: key,
    input_sha256: "a".repeat(64),
    definition_sha256: "b".repeat(64),
    child_run_id: null,
    consumption: "not_submitted_to_model",
    checkpoint_id: null,
    source_status: "started_no_terminal_receipt",
    child_attempt_id: null,
    started_at: null,
    finished_at: null,
    safe_error_code: null,
    input_refs: [],
    derived_input_refs: [],
    output_refs: [],
    containment: "not_applicable",
    settlement: "not_observed",
    model_requests: [
      {
        stage_id: key,
        input_revision_id: key,
        input_sha256: "a".repeat(64),
        state: "submission_uncertain",
        model_operation_id: owner,
        transport_id: workflow,
        request_sha256: "c".repeat(64),
      },
    ],
    qualification: "retained_stage_metadata_not_live_status_or_result_release",
  };
  const html = renderToStaticMarkup(
    createElement(WorkflowHistoryView, {
      workflow: root,
      stage,
      selection: {
        schema_version: "mineagent.workflow-input-snapshot.v1",
        owner_id: owner,
        workflow_id: workflow,
        revision_id: owner,
        refs: [],
      },
    }),
  );
  expect(html).toContain("continues to use its old version");
  expect(html).toContain("delivery/response not confirmed");
  expect(html).toContain("not proof of a still-running process");
  expect(html).not.toContain("Provider response completed");
});

it("CAS rejection may be acknowledged explicitly; ambiguous failures keep the original command", async () => {
  const {
    submitWorkflowMutation,
    loadWorkflowMutation,
    clearRejectedWorkflowMutation,
  } = await import("../../src/lib/research-workflows");
  const body = {
    schema_version: "mineagent.workflow-select-inputs.v1" as const,
    workflow_id: workflow,
    expected_revision: 1,
    idempotency_key: key,
    refs: [],
  };
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: owner })))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ code: "workflow_revision_conflict" }), {
          status: 409,
        }),
      ),
  );
  await expect(submitWorkflowMutation(owner, body)).rejects.toMatchObject({
    status: 409,
    code: "workflow_revision_conflict",
  });
  expect(loadWorkflowMutation(owner, workflow)).toEqual({
    state: "pending",
    body,
  });
  expect(() =>
    clearRejectedWorkflowMutation(owner, workflow, { status: 503 }),
  ).toThrow("unresolved");
  clearRejectedWorkflowMutation(owner, workflow, {
    status: 409,
    code: "workflow_revision_conflict",
  });
  expect(loadWorkflowMutation(owner, workflow)).toEqual({ state: "empty" });
});

it("replacing a manual version preserves selected prompt ordering and never edits another origin", async () => {
  const { replaceWorkflowInput } =
    await import("../../src/features/workflow-selection");
  const manual = {
    origin: "manual_user_submission" as const,
    record_id: owner,
    version_id: owner,
    content_sha256: "a".repeat(64),
  };
  const external = {
    origin: "normalized_external_proposal" as const,
    record_id: key,
    version_id: key,
    content_sha256: "b".repeat(64),
  };
  const next = {
    ...manual,
    version_id: workflow,
    content_sha256: "c".repeat(64),
  };
  expect(replaceWorkflowInput([external, manual], next)).toEqual([
    external,
    next,
  ]);
  expect(manual.version_id).toBe(owner);
});
it("unknown or oversized selection is refused rather than truncated", async () => {
  const { replaceWorkflowInput } =
    await import("../../src/features/workflow-selection");
  const ref = {
    origin: "normalized_external_proposal" as const,
    record_id: owner,
    version_id: key,
    content_sha256: "a".repeat(64),
  };
  expect(() => replaceWorkflowInput([], { ...ref, origin: "unknown" })).toThrow(
    "Unknown",
  );
  expect(() =>
    replaceWorkflowInput(
      Array.from({ length: 32 }, (_, i) => ({ ...ref, record_id: String(i) })),
      ref,
    ),
  ).toThrow("no truncation");
});
it("selection editor links normal steer management and explicitly separates saved from applied", async () => {
  const { WorkflowSelectionEditor } =
    await import("../../src/features/workflow-selection");
  const html = renderToStaticMarkup(
    createElement(
      QueryClientProvider,
      { client: new QueryClient() },
      createElement(WorkflowSelectionEditor, {
        ownerId: owner,
        workflow: root,
        refresh: vi.fn(),
        selection: {
          schema_version: "mineagent.workflow-input-snapshot.v1",
          owner_id: owner,
          workflow_id: workflow,
          revision_id: owner,
          refs: [],
        },
      }),
    ),
  );
  expect(html).toContain('href="/rd-steers"');
  expect(html).toContain("CRUD save alone is not applied");
  expect(html).toContain("Explicit ordered draft — not applied");
});

it("missing workflow router is unavailable, not a legacy fallback", async () => {
  const { readWorkflowAvailability } =
    await import("../../src/features/research-workflow-page");
  const fetcher = vi.fn(
    async (_url: string) => new Response("{}", { status: 404 }),
  );
  vi.stubGlobal("fetch", fetcher);
  expect(await readWorkflowAvailability()).toEqual({
    can_read: false,
    preflight_available: false,
    structured_preflight_available: false,
    reason_code: "workflow_routes_not_installed",
  });
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0][0]).toBe(
    "/api/v1/research-workflows/capabilities",
  );
});
it("workflow reads preserve configured SUBPATH and do not fetch root or legacy routes", async () => {
  vi.resetModules();
  vi.stubGlobal("document", { querySelector: () => ({ content: "/mine" }) });
  const fetcher = vi.fn(
    async (_url: string) => new Response(JSON.stringify(root)),
  );
  vi.stubGlobal("fetch", fetcher);
  const { readWorkflow } = await import("../../src/lib/research-workflows");
  await readWorkflow(workflow);
  expect(fetcher.mock.calls[0][0]).toBe(
    `/mine/api/v1/research-workflows/${workflow}`,
  );
  vi.resetModules();
});

it("automatic feedback remains distinct from manual selection and is explicitly viewable", () => {
  const ref = {
    origin: "experiment_feedback_proposal" as const,
    record_id: owner,
    version_id: key,
    content_sha256: "a".repeat(64),
  };
  const stage: WorkflowStageHistory = {
    stage_id: key,
    ordinal: 3,
    kind: "rd_factor_generation",
    input_revision_id: key,
    input_sha256: "b".repeat(64),
    definition_sha256: "c".repeat(64),
    child_run_id: null,
    consumption: "not_observed",
    checkpoint_id: null,
    source_status: "not_recorded",
    child_attempt_id: null,
    started_at: null,
    finished_at: null,
    input_refs: [ref],
    automatic_feedback_refs: [ref],
    predecessor_checkpoint_id: owner,
    containment: "not_observed",
    settlement: "not_observed",
    qualification: "retained_stage_metadata_not_live_status_or_result_release",
  };
  const html = renderToStaticMarkup(
    createElement(WorkflowHistoryView, { workflow: root, stage }),
  );
  expect(html).toContain("Automatically included experiment feedback");
  expect(html).toContain("not manual guidance or a user approval");
  expect(html).toContain(owner);
  expect(html).toContain("View feedback output");
  expect(html).not.toContain("<pre>");
});

it("stage output identities link to guarded exact results without implicit release", () => {
  const stage: WorkflowStageHistory = {
    stage_id: key,
    ordinal: 1,
    kind: "rd_factor_generation",
    input_revision_id: key,
    input_sha256: "a".repeat(64),
    definition_sha256: "b".repeat(64),
    child_run_id: owner,
    child_attempt_id: null,
    consumption: "not_observed",
    checkpoint_id: null,
    source_status: "succeeded",
    started_at: null,
    finished_at: null,
    containment: "positive_stop",
    settlement: "settled",
    output_refs: [
      {
        workflow_id: workflow,
        stage_id: key,
        child_run_id: owner,
        output_id: key,
        output_kind: "direct_analysis",
        content_sha256: "c".repeat(64),
      },
      {
        workflow_id: workflow,
        stage_id: key,
        child_run_id: owner,
        output_id: workflow,
        output_kind: "structured_execution",
        content_sha256: "d".repeat(64),
      },
    ],
  };
  const html = renderToStaticMarkup(
    createElement(WorkflowHistoryView, { workflow: root, stage }),
  );
  expect(html).toContain(`/engine-runs/${owner}/analyses/${key}`);
  expect(html).toContain(
    `/engine-runs/structured-search/runs/${owner}?execution_id=${workflow}`,
  );
  expect(html).toContain("not a grant of result disclosure");
  expect(html).toContain("c".repeat(64));
  expect(html).not.toContain("/release");
  expect(html).not.toContain("<button");
});

it.each(["failed", "cancelled"])(
  "%s child metadata never claims physical completion",
  (status) => {
    const html = renderToStaticMarkup(
      createElement(WorkflowHistoryView, {
        workflow: {
          ...root,
          blocked_reason: `workflow_child_${status}_unverified`,
        },
      }),
    );
    expect(html).toContain("Child terminal status is recorded metadata only");
    expect(html).toContain("safe checkpoint are not verified");
    expect(html).toContain("retry, or next child is authorized");
  },
);

it.each(["pause_requested", "cancel_requested"] as const)(
  "%s describes admission-boundary semantics rather than immediate stop",
  (status) => {
    const html = renderToStaticMarkup(
      createElement(WorkflowHistoryView, {
        workflow: { ...root, status, blocked_reason: null },
      }),
    );
    expect(html).toContain("already admitted");
    expect(html).toContain("may still start from its");
    expect(html).toContain("verified physical stop and settlement");
    expect(html).toContain("No subsequent stage is authorized");
  },
);

it("v2 retry uses its original structured endpoint and cannot replace a v1 pending body", async () => {
  const body = JSON.parse(envelope.body_json);
  body.schema_version = "mineagent.workflow-public-submit.v2";
  body.request.schema_version = "mineagent.workflow-public-request.v2";
  const structured = { ...envelope, body_json: JSON.stringify(body) };
  saveWorkflowEnvelope(envelope);
  expect(() => saveWorkflowEnvelope(structured)).toThrow("unresolved");
  sessionStorage.removeItem(
    `mineagent.workflow-submit.v1:${APP_SUBPATH}:${owner}`,
  );
  saveWorkflowEnvelope(structured);
  expect(loadWorkflowEnvelope(owner)).toEqual({
    state: "pending",
    envelope: structured,
  });
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ id: owner })))
    .mockRejectedValueOnce(new Error("uncertain v2"));
  vi.stubGlobal("fetch", fetcher);
  await expect(submitWorkflowEnvelope(structured)).rejects.toThrow(
    "uncertain v2",
  );
  expect(String(fetcher.mock.calls[1][0])).toContain(
    "/research-workflows/structured",
  );
  expect(fetcher.mock.calls[1][1].body).toBe(structured.body_json);
  expect(loadWorkflowEnvelope(owner)).toEqual({
    state: "pending",
    envelope: structured,
  });
});
it("mixed workflow request versions are retained as invalid, never silently reinterpreted", () => {
  const body = JSON.parse(envelope.body_json);
  body.schema_version = "mineagent.workflow-public-submit.v2";
  expect(() =>
    saveWorkflowEnvelope({ ...envelope, body_json: JSON.stringify(body) }),
  ).toThrow("nothing was submitted");
  expect(loadWorkflowEnvelope(owner)).toEqual({ state: "invalid" });
});
it("structured workflow view honestly waits for release without a child or result", () => {
  const html = renderToStaticMarkup(
    createElement(WorkflowHistoryView, {
      workflow: {
        ...root,
        schema_version: "mineagent.workflow-view.v2",
        blocked_reason: "workflow_structured_release_required",
      },
    }),
  );
  expect(html).toContain("Waiting for explicit release");
  expect(html).toContain("No current stage recorded");
  expect(html).not.toContain("Open exact structured_execution");
});
it("structured capability remains independently closed even when v1 is available", () => {
  const html = renderToStaticMarkup(
    createElement(
      QueryClientProvider,
      { client: new QueryClient() },
      createElement(WorkflowEntry, {
        ownerId: owner,
        available: true,
        reason: "ready",
        onCreated: vi.fn(),
      }),
    ),
  );
  expect(html).toContain("Structured workflow preflight is unavailable");
  expect(html).toContain(
    "Add one terminal operator-backed structured OE stage",
  );
  expect(html).not.toContain('aria-label="Terminal structured OE declaration"');
});
it("OE stage history links only real retained outputs and separates model evidence from settlement", () => {
  const stage = {
    stage_id: key,
    ordinal: 3,
    kind: "structured_factor_execution" as const,
    input_revision_id: key,
    input_sha256: "a".repeat(64),
    definition_sha256: "b".repeat(64),
    child_run_id: workflow,
    child_attempt_id: null,
    started_at: null,
    finished_at: null,
    consumption: "selected_not_submitted" as const,
    checkpoint_id: null,
    source_status: "queued" as const,
    input_refs: [],
    derived_input_refs: [],
    model_requests: [],
    containment: "not_observed" as const,
    settlement: "not_observed" as const,
    output_refs: [
      {
        workflow_id: workflow,
        stage_id: key,
        child_run_id: workflow,
        output_kind: "structured_execution" as const,
        output_id: owner,
        content_sha256: "c".repeat(64),
      },
    ],
  };
  const html = renderToStaticMarkup(
    createElement(WorkflowHistoryView, {
      workflow: { ...root, schema_version: "mineagent.workflow-view.v2" },
      stage,
    }),
  );
  expect(html).toContain("Actual structured OE child stage");
  expect(html).toContain("No model request evidence is shown");
  expect(html).toContain(
    `/engine-runs/structured-search/runs/${workflow}?execution_id=${owner}`,
  );
  expect(html).not.toContain("Provider response completed");
});
