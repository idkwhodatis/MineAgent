import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  clearEngineSubmission,
  definitelyNotAccepted,
  loadEngineSubmission,
  prepareEngineSubmission,
  saveEngineSubmission,
  submissionBody,
} from "../../src/lib/engine-submission-draft";
import type { RdPreflight } from "../../src/client/types.gen";
const owner = "00000000-0000-4000-8000-000000000001",
  other = "00000000-0000-4000-8000-000000000002";
const preflight = {
  can_submit: true,
  data_eligible: true,
  request_sha256: "a".repeat(64),
  preflight_sha256: "b".repeat(64),
  normalized_request: {
    schema_version: "mineagent.rdagent-submission.v1",
    purpose: "factor_research",
    engine: "rdagent",
    dataset_version_id: "00000000-0000-4000-8000-000000000003",
  },
} as RdPreflight;
let values: Map<string, string>;
beforeEach(() => {
  values = new Map();
  vi.stubGlobal("sessionStorage", {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  });
});
afterEach(() => vi.unstubAllGlobals());
it("reload retains exact owner/key/body and does not mint another request", () => {
  const pending = prepareEngineSubmission(owner, preflight);
  expect(saveEngineSubmission(pending)).toBe(true);
  expect(loadEngineSubmission(owner)).toEqual({
    state: "pending",
    envelope: pending,
  });
  expect(loadEngineSubmission(other)).toEqual({ state: "empty" });
  expect(submissionBody(pending).preflight_sha256).toBe(
    preflight.preflight_sha256,
  );
  expect(saveEngineSubmission(prepareEngineSubmission(owner, preflight))).toBe(
    false,
  );
  expect(loadEngineSubmission(owner)).toEqual({
    state: "pending",
    envelope: pending,
  });
});
it("only the exact accepted or definitively rejected envelope may clear", () => {
  const pending = prepareEngineSubmission(owner, preflight);
  saveEngineSubmission(pending);
  expect(clearEngineSubmission({ ...pending, key: other })).toBe(false);
  expect(clearEngineSubmission(pending)).toBe(true);
  expect(loadEngineSubmission(owner).state).toBe("empty");
});
it("malformed or inaccessible storage blocks replacement rather than assuming no request", () => {
  values.set(`mineagent.rd-submit.v1::${owner}`, "broken");
  expect(loadEngineSubmission(owner).state).toBe("invalid");
  expect(saveEngineSubmission(prepareEngineSubmission(owner, preflight))).toBe(
    false,
  );
  vi.stubGlobal("sessionStorage", {
    getItem: () => {
      throw new Error("unavailable");
    },
  });
  expect(loadEngineSubmission(owner).state).toBe("invalid");
});
it("closed or ineligible preflight never prepares a POST envelope", () => {
  expect(() =>
    prepareEngineSubmission(owner, { ...preflight, can_submit: false }),
  ).toThrow();
  expect(() =>
    prepareEngineSubmission(owner, { ...preflight, data_eligible: false }),
  ).toThrow();
});
it.each([
  "idempotency_conflict",
  "neutral_busy",
  "engine_submission_schema_unavailable",
  "unknown",
])("retains uncertainty for %s", (code) => {
  expect(definitelyNotAccepted({ status: 409, code })).toBe(false);
});
it.each([
  "preflight_changed",
  "engine_request_ineligible",
  "run_quota",
  "engine_submission_not_enabled",
  "engine_runtime_unconfigured",
  "engine_runtime_unsupported",
  "runtime_profile_changed",
])("permits re-preflight only after explicit rejection %s", (code) => {
  expect(definitelyNotAccepted({ status: 409, code })).toBe(true);
  for (const status of [401, 403, 500, 503])
    expect(definitelyNotAccepted({ status, code })).toBe(false);
});
