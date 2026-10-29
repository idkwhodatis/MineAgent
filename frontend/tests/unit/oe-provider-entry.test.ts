import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import {
  OEProviderEntry,
  emptyOEDeclaration,
} from "../../src/features/oe-provider-entry";
import {
  clearOESubmission,
  clearOEReceipt,
  loadOESubmission,
  loadOEReceipt,
  oeDefinitelyNotAccepted,
  oeModelsOptions,
  oeSnapshotsOptions,
  prepareOESubmission,
  saveOESubmission,
  saveOEReceipt,
  submitSavedOE,
} from "../../src/lib/oe-provider-entry";
import type { ProviderStructuredPreflight } from "../../src/client/types.gen";
const { requestCall } = vi.hoisted(() => ({ requestCall: vi.fn() }));
vi.mock("../../src/lib/oe-provider-api", () => ({
  oeSubmit: requestCall,
  oeModels: vi.fn(),
  oeSnapshots: vi.fn(),
  oePreflight: vi.fn(),
  oeRun: vi.fn(),
}));
const owner = "30000000-0000-4000-8000-000000000001",
  other = "30000000-0000-4000-8000-000000000002";
const request = {
  ...emptyOEDeclaration(),
  snapshot_id: "30000000-0000-4000-8000-000000000003",
  model_profile_id: "operator",
  model_binding_sha256: "a".repeat(64),
  seed_parameters: {
    threshold: 0,
    top_k: 1,
    maximum_weight: 1,
    maximum_gross_weight: 1,
  },
  objective: {
    ...emptyOEDeclaration().objective,
    window: { start: "2024-03-13", end: "2024-03-15" },
    initial_cash: 100000,
  },
  budget: {
    calls: 10,
    tokens: 1000,
    mutation_slots: 1,
    evaluator_executions: 2,
    wall_milliseconds: 10000,
    storage_bytes: 10000,
    process_slots: 4,
    cost_microunits: 1000,
  },
};
const preflight: ProviderStructuredPreflight = {
  normalized_request: request,
  request_sha256: "b".repeat(64),
  preflight_sha256: "c".repeat(64),
  fixed_input_sha256: "d".repeat(64),
  runtime_profile_sha256: "e".repeat(64),
  model_binding: {
    profile_id: "operator",
    profile_sha256: "f".repeat(64),
    model: "chat",
    max_output_tokens: 100,
    request_deadline_ms: 1000,
  },
  required_budget: request.budget,
  required_process_units: 4,
  data_eligible: true,
  can_submit: true,
  reasons: [],
  data_qualification: "synthetic_by_construction",
};
const receipt = () => ({
  schema_version:
    "mineagent.oe-structured-provider-submission-receipt.v1" as const,
  id: "30000000-0000-4000-8000-000000000004",
  run_id: "30000000-0000-4000-8000-000000000005",
  origin: "authenticated_user_submission" as const,
  origin_user_id: owner,
  request_sha256: preflight.request_sha256,
  preflight_sha256: preflight.preflight_sha256,
  normalized_request: request,
  required_process_units: 4 as const,
});
let stored: Map<string, string>;
beforeEach(() => {
  stored = new Map();
  requestCall.mockReset();
  vi.stubGlobal("sessionStorage", {
    getItem: (key: string) => stored.get(key) ?? null,
    setItem: (key: string, value: string) => stored.set(key, value),
    removeItem: (key: string) => stored.delete(key),
  });
});
afterEach(() => vi.unstubAllGlobals());
it("keeps original key/body across reload and refuses to replace pending data", () => {
  const envelope = prepareOESubmission(owner, preflight);
  expect(saveOESubmission(envelope)).toBe(true);
  expect(loadOESubmission(owner)).toEqual({ state: "pending", envelope });
  expect(saveOESubmission({ ...envelope, key: other })).toBe(false);
  expect(clearOESubmission({ ...envelope, key: other })).toBe(false);
  expect(loadOESubmission(other)).toEqual({ state: "empty" });
});
it("malformed persisted identity blocks replacement", () => {
  const envelope = prepareOESubmission(owner, preflight);
  saveOESubmission(envelope);
  stored.set([...stored.keys()][0], '{"unknown":true}');
  expect(loadOESubmission(owner)).toEqual({ state: "invalid" });
  expect(saveOESubmission(envelope)).toBe(false);
});
it("storage failure never claims the envelope was saved", () => {
  vi.stubGlobal("sessionStorage", {
    getItem: () => null,
    setItem: () => {
      throw new Error("quota");
    },
  });
  expect(saveOESubmission(prepareOESubmission(owner, preflight))).toBe(false);
  expect(requestCall).not.toHaveBeenCalled();
});
it("retries use the exact same owner key/body and do not invent current lifecycle", async () => {
  const envelope = prepareOESubmission(owner, preflight);
  saveOESubmission(envelope);
  requestCall.mockResolvedValue(receipt());
  const first = await submitSavedOE(envelope),
    second = await submitSavedOE(envelope);
  expect(first).toEqual(second);
  expect(first).not.toHaveProperty("status");
  expect(requestCall.mock.calls[0]).toEqual(requestCall.mock.calls[1]);
  expect(requestCall.mock.calls[0][0]).toEqual(JSON.parse(envelope.body_json));
  expect(requestCall.mock.calls[0][1]).toBe(envelope.key);
  expect(loadOESubmission(owner).state).toBe("pending");
});
it.each(["owner", "request", "preflight", "snapshot", "model", "units"])(
  "withholds mismatched receipt %s and preserves original pending request",
  async (field) => {
    const envelope = prepareOESubmission(owner, preflight);
    saveOESubmission(envelope);
    const value = structuredClone(receipt());
    if (field === "owner") value.origin_user_id = other;
    if (field === "request") value.request_sha256 = "f".repeat(64);
    if (field === "preflight") value.preflight_sha256 = "f".repeat(64);
    if (field === "snapshot") value.normalized_request.snapshot_id = other;
    if (field === "model")
      value.normalized_request.model_binding_sha256 = "f".repeat(64);
    if (field === "units") Object.assign(value, { required_process_units: 6 });
    requestCall.mockResolvedValue(value);
    await expect(submitSavedOE(envelope)).rejects.toThrow();
    expect(loadOESubmission(owner)).toEqual({ state: "pending", envelope });
  },
);
it("only the two confirmed post-replay rejections permit draft editing", () => {
  expect(
    oeDefinitelyNotAccepted({ status: 409, code: "preflight_changed" }),
  ).toBe(true);
  expect(oeDefinitelyNotAccepted({ status: 429, code: "run_quota" })).toBe(
    true,
  );
  for (const code of [
    "idempotency_conflict",
    "neutral_busy",
    "engine_request_ineligible",
    "runtime_disabled",
    "schema_unavailable",
  ])
    expect(oeDefinitelyNotAccepted({ status: 409, code })).toBe(false);
  expect(
    oeDefinitelyNotAccepted({ status: 500, code: "preflight_changed" }),
  ).toBe(false);
  expect(oeDefinitelyNotAccepted(new Error("network"))).toBe(false);
});
it("ineligible preflight cannot produce a pending envelope", () => {
  expect(() =>
    prepareOESubmission(owner, { ...preflight, can_submit: false }),
  ).toThrow();
  expect(() =>
    prepareOESubmission(owner, { ...preflight, model_binding: null }),
  ).toThrow();
});
it("persists confirmed receipt separately, owner-scoped, without making it live status", () => {
  expect(saveOEReceipt(owner, receipt())).toBe(true);
  expect(loadOEReceipt(owner)).toEqual(receipt());
  expect(loadOEReceipt(other)).toBeNull();
  expect(saveOEReceipt(other, receipt())).toBe(false);
  expect(clearOEReceipt(owner, other)).toBe(false);
  expect(clearOEReceipt(owner, receipt().id)).toBe(true);
});
it("owner-specific model and snapshot caches never share identities", () => {
  expect(oeModelsOptions(owner).queryKey).not.toEqual(
    oeModelsOptions(other).queryKey,
  );
  expect(oeSnapshotsOptions(owner, 0).queryKey).not.toEqual(
    oeSnapshotsOptions(other, 0).queryKey,
  );
});
it("unregistered entry remains closed and never falls back to fixture submission", () => {
  const html = renderToStaticMarkup(
    createElement(
      QueryClientProvider,
      { client: new QueryClient() },
      createElement(OEProviderEntry, { ownerId: owner }),
    ),
  );
  expect(html).toContain("maintained fixture is not a substitute");
  expect(html).not.toContain("Submit exact checked OE request");
  expect(requestCall).not.toHaveBeenCalled();
});
