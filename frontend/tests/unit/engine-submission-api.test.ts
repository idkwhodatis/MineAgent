import { afterEach, expect, it, vi } from "vitest";
import {
  cancelEngineRun,
  engineRunActive,
  submitSavedEngineRequest,
} from "../../src/lib/engine-submission-api";
import type { EngineSubmissionEnvelope } from "../../src/lib/engine-submission-draft";
const owner = "00000000-0000-4000-8000-000000000001",
  run = "00000000-0000-4000-8000-000000000002";
const envelope: EngineSubmissionEnvelope = {
  version: 1,
  owner,
  key: owner,
  request_sha256: "a".repeat(64),
  preflight_sha256: "b".repeat(64),
  body_json: JSON.stringify({
    schema_version: "mineagent.rdagent-submission.v1",
    purpose: "factor_research",
    engine: "rdagent",
    dataset_version_id: owner,
    preflight_sha256: "b".repeat(64),
  }),
};
const accepted = {
  schema_version: "mineagent.rdagent-submission-receipt.v1",
  run_id: run,
  plan_id: owner,
  request_sha256: envelope.request_sha256,
  preflight_sha256: envelope.preflight_sha256,
  accepted: true,
  replayed: true,
  status: "succeeded",
};
afterEach(() => vi.unstubAllGlobals());
it("uses exact saved key/body and accepts authoritative terminal replay without inventing queued", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue(new Response(JSON.stringify(accepted)));
  vi.stubGlobal("fetch", fetcher);
  expect((await submitSavedEngineRequest(envelope)).status).toBe("succeeded");
  expect(fetcher.mock.calls[0][0]).toBe("/api/v1/engine-runs");
  expect(fetcher.mock.calls[0][1].headers["Idempotency-Key"]).toBe(
    envelope.key,
  );
  expect(fetcher.mock.calls[0][1].body).toBe(envelope.body_json);
});
it.each([
  { ...accepted, request_sha256: "c".repeat(64) },
  { ...accepted, accepted: false },
  { ...accepted, status: "completed" },
])(
  "never treats a mismatched or fabricated receipt as acceptance",
  async (body) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify(body))),
    );
    await expect(submitSavedEngineRequest(envelope)).rejects.toThrow();
  },
);
it("active cancellation is pending, never evidence of physical stop", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          schema_version: "mineagent.rdagent-control-receipt.v1",
          run_id: run,
          action: "cancel",
          status: "cancel_requested",
          pending: true,
        }),
      ),
    ),
  );
  expect((await cancelEngineRun(run)).pending).toBe(true);
  expect(engineRunActive("cancel_requested")).toBe(true);
  expect(engineRunActive("cancelled")).toBe(false);
});
it("contradictory cancellation status is not displayed as confirmed", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          schema_version: "mineagent.rdagent-control-receipt.v1",
          run_id: run,
          action: "cancel",
          status: "cancel_requested",
          pending: false,
        }),
      ),
    ),
  );
  await expect(cancelEngineRun(run)).rejects.toThrow();
});

it("preserves operator SUBPATH for creation and cancellation transport", async () => {
  vi.resetModules();
  vi.stubGlobal("document", {
    querySelector: () => ({ content: "/research" }),
  });
  const fetcher = vi.fn().mockImplementation((path: string) =>
    Promise.resolve(
      new Response(
        JSON.stringify(
          path.endsWith("/cancel")
            ? {
                schema_version: "mineagent.rdagent-control-receipt.v1",
                run_id: run,
                action: "cancel",
                status: "cancelled",
                pending: false,
              }
            : accepted,
        ),
      ),
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  const prefixed = await import("../../src/lib/engine-submission-api");
  await prefixed.submitSavedEngineRequest(envelope);
  await prefixed.cancelEngineRun(run);
  expect(fetcher.mock.calls.map((call) => call[0])).toEqual([
    "/research/api/v1/engine-runs",
    `/research/api/v1/engine-runs/${run}/cancel`,
  ]);
  vi.resetModules();
});
