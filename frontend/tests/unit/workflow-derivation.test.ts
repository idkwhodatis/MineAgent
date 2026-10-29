import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";
import {
  WorkflowDerivation,
  derivationOperation,
  readWorkflowDerivation,
} from "../../src/features/workflow-derivation";
import { factorRequest } from "../../src/lib/factor-api";
import type { InputDerivationView } from "../../src/input-audit-contracts/types.gen";
vi.mock("../../src/lib/factor-api", () => ({ factorRequest: vi.fn() }));
const receipt: InputDerivationView = {
  derivation_id: "receipt",
  workflow_id: "workflow",
  stage_id: "stage",
  operation: "external_normalization",
  source_sha256: "a".repeat(64),
  raw_object_id: "object",
  raw_input_sha256: "b".repeat(64),
  mapping_sha256: "c".repeat(64),
  status: "prepared",
  error_code: null,
  prepared_at: "2026-10-27T06:00:00Z",
  started_at: null,
  terminal_at: null,
  outputs: [],
  qualification: "receipt_metadata_not_live_status_or_content_approval",
};
it("reads actual selected historical stage metadata, never raw content or a current-stage substitute", async () => {
  vi.mocked(factorRequest).mockReset().mockResolvedValue(receipt);
  const signal = new AbortController().signal;
  expect(
    await readWorkflowDerivation(
      "workflow",
      "stage",
      "external_normalization",
      signal,
    ),
  ).toBe(receipt);
  expect(factorRequest).toHaveBeenCalledExactlyOnceWith(
    "/research-workflows/workflow/stages/stage/input-derivation",
    { signal },
  );
  expect(derivationOperation("external_normalization")).toBe(
    "external_normalization",
  );
  expect(derivationOperation("experiment_feedback_projection")).toBe(
    "experiment_feedback_projection",
  );
  expect(derivationOperation("rd_factor_generation")).toBeUndefined();
});
it("rejects cross-stage/workflow/operation or nonmetadata receipts without falling back", async () => {
  for (const patch of [
    { workflow_id: "other" },
    { stage_id: "new-current" },
    { operation: "experiment_feedback_projection" },
    { qualification: "approved" },
  ]) {
    vi.mocked(factorRequest)
      .mockReset()
      .mockResolvedValue({ ...receipt, ...patch });
    await expect(
      readWorkflowDerivation("workflow", "stage", "external_normalization"),
    ).rejects.toThrow("scope differs");
    expect(factorRequest).toHaveBeenCalledTimes(1);
  }
});
it("absent metadata remains absent while authorization and integrity errors remain errors", async () => {
  vi.mocked(factorRequest).mockReset().mockRejectedValue({ status: 404 });
  expect(
    await readWorkflowDerivation("workflow", "stage", "external_normalization"),
  ).toBeNull();
  for (const status of [401, 409, 503]) {
    vi.mocked(factorRequest).mockReset().mockRejectedValue({ status });
    await expect(
      readWorkflowDerivation("workflow", "stage", "external_normalization"),
    ).rejects.toEqual({ status });
    expect(factorRequest).toHaveBeenCalledTimes(1);
  }
});

it("mounts retained input actions after historical metadata reload, without fetching private bytes", () => {
  vi.mocked(factorRequest).mockReset();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  client.setQueryData(
    [
      "workflow",
      "owner",
      "workflow",
      "stage",
      "stage",
      "derivation",
      "external_normalization",
    ],
    receipt,
  );
  const html = renderToStaticMarkup(
    createElement(
      QueryClientProvider,
      { client },
      createElement(WorkflowDerivation, {
        ownerId: "owner",
        workflowId: "workflow",
        stage: { stage_id: "stage", kind: "external_normalization" },
      }),
    ),
  );
  expect(html).toContain("Input derivation audit");
  expect(html).toContain("Prepared input");
  expect(html).toContain("View input");
  expect(html).toContain("Download retained input");
  expect(html).toContain("No derived version references recorded");
  expect(html).not.toContain("<pre");
  expect(factorRequest).not.toHaveBeenCalled();
  client.clear();
});
