import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type {
  EngineCapability,
  RdModelProfileOption,
  RdProviderPreflight,
} from "../../src/client/types.gen";
import {
  providerPreflightSearch,
  seededPreflightHref,
  generationCanCreate,
  preflightMatchesSelection,
} from "../../src/lib/rd-provider-inputs";
import {
  definitelyNotAccepted,
  prepareEngineSubmission,
  saveEngineSubmission,
  loadEngineSubmission,
  submissionBody,
} from "../../src/lib/engine-submission-draft";
import { submitSavedEngineRequest } from "../../src/lib/engine-submission-api";
const owner = "00000000-0000-4000-8000-000000000001";
const version = "00000000-0000-4000-8000-000000000002";
const other = "00000000-0000-4000-8000-000000000003";
const preflight = {
  schema_version: "mineagent.rdagent-provider-preflight.v1",
  can_submit: true,
  data_eligible: true,
  request_sha256: "a".repeat(64),
  preflight_sha256: "b".repeat(64),
  steer_snapshot_sha256: "c".repeat(64),
  model_binding_sha256: "e".repeat(64),
  model_binding: { profile_id: "test_model" },
  normalized_request: {
    schema_version: "mineagent.rdagent-provider-submission.v1",
    purpose: "factor_research",
    engine: "rdagent",
    dataset_version_id: other,
    model_profile_id: "test_model",
    selected_steer_version_ids: [version],
  },
  steer_snapshot: { owner_id: owner, versions: [{ version_id: version }] },
} as RdProviderPreflight;
beforeEach(() => {
  const storage = new Map<string, string>();
  vi.stubGlobal("sessionStorage", {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
    removeItem: (key: string) => storage.delete(key),
  });
});
afterEach(() => vi.unstubAllGlobals());
it("fixture availability never enables configured models or unlisted selections", () => {
  const capability = {
    can_create: true,
    configured_model_can_create: false,
  } as EngineCapability;
  const model = { can_create: true } as RdModelProfileOption;
  expect(generationCanCreate("fixture", capability, undefined)).toBe(true);
  expect(generationCanCreate("configured", capability, model)).toBe(false);
  expect(
    generationCanCreate(
      "configured",
      { ...capability, configured_model_can_create: true },
      undefined,
    ),
  ).toBe(false);
  expect(
    generationCanCreate(
      "configured",
      { ...capability, configured_model_can_create: true },
      { ...model, can_create: false },
    ),
  ).toBe(false);
});
it("preflight must match exact mode/model/parent/ordered steer selection", () => {
  expect(
    preflightMatchesSelection(preflight, "configured", "test_model", "", [
      version,
    ]),
  ).toBe(true);
  expect(
    preflightMatchesSelection(preflight, "fixture", "test_model", "", [
      version,
    ]),
  ).toBe(false);
  expect(
    preflightMatchesSelection(preflight, "configured", "other_model", "", [
      version,
    ]),
  ).toBe(false);
  expect(
    preflightMatchesSelection(preflight, "configured", "test_model", other, [
      version,
    ]),
  ).toBe(false);
  expect(
    preflightMatchesSelection(preflight, "configured", "test_model", "", [
      other,
      version,
    ]),
  ).toBe(false);
});
it("foreign or reordered preflight snapshot cannot be persisted as this owner's input", () => {
  expect(() =>
    prepareEngineSubmission(owner, {
      ...preflight,
      steer_snapshot: { ...preflight.steer_snapshot, owner_id: other },
    }),
  ).toThrow();
  expect(() =>
    prepareEngineSubmission(owner, {
      ...preflight,
      steer_snapshot: { owner_id: owner, versions: [] },
    }),
  ).toThrow();
});
it("accepted-version retry survives current steer changes without replacing saved body", async () => {
  const envelope = prepareEngineSubmission(owner, preflight);
  expect(saveEngineSubmission(envelope)).toBe(true);
  const original = envelope.body_json;
  expect(
    saveEngineSubmission(
      prepareEngineSubmission(owner, {
        ...preflight,
        preflight_sha256: "d".repeat(64),
      }),
    ),
  ).toBe(false);
  expect(loadEngineSubmission(owner)).toEqual({ state: "pending", envelope });
  const receipt = {
    schema_version: "mineagent.rdagent-provider-submission-receipt.v1",
    run_id: other,
    plan_id: version,
    request_sha256: envelope.request_sha256,
    preflight_sha256: envelope.preflight_sha256,
    model_binding_sha256: "e".repeat(64),
    accepted: true,
    replayed: true,
    status: "succeeded",
  };
  const fetcher = vi
    .fn()
    .mockResolvedValue(new Response(JSON.stringify(receipt)));
  vi.stubGlobal("fetch", fetcher);
  expect((await submitSavedEngineRequest(envelope)).status).toBe("succeeded");
  expect(fetcher.mock.calls[0][1].body).toBe(original);
  expect(fetcher.mock.calls[0][1].headers["Idempotency-Key"]).toBe(
    envelope.key,
  );
});
it.each([
  { schema_version: "mineagent.rdagent-submission.v1" },
  { schema_version: "unknown" },
  { selected_steer_version_ids: [version, version] },
  { model_profile_id: "https://caller-endpoint" },
  { parent_factor_version_id: other },
])(
  "refuses protocol drift instead of inferring a family from fields",
  (change) => {
    const envelope = prepareEngineSubmission(owner, preflight);
    expect(() =>
      submissionBody({
        ...envelope,
        body_json: JSON.stringify({
          ...JSON.parse(envelope.body_json),
          ...change,
        }),
      }),
    ).toThrow();
  },
);
it("fixture-shaped reply cannot acknowledge a provider request", async () => {
  const envelope = prepareEngineSubmission(owner, preflight);
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          schema_version: "mineagent.rdagent-submission-receipt.v1",
          run_id: other,
          plan_id: version,
          request_sha256: envelope.request_sha256,
          preflight_sha256: envelope.preflight_sha256,
          accepted: true,
          replayed: false,
          status: "queued",
        }),
      ),
    ),
  );
  await expect(submitSavedEngineRequest(envelope)).rejects.toThrow();
});

it("unknown model commitment cannot become a submit envelope", () => {
  expect(() =>
    prepareEngineSubmission(owner, {
      ...preflight,
      model_binding_sha256: null,
    }),
  ).toThrow();
  expect(() =>
    prepareEngineSubmission(owner, { ...preflight, model_binding: null }),
  ).toThrow();
});
it("changed model commitment in receipt leaves the exact retry identity unresolved", async () => {
  const envelope = prepareEngineSubmission(owner, preflight);
  saveEngineSubmission(envelope);
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          schema_version: "mineagent.rdagent-provider-submission-receipt.v1",
          run_id: other,
          plan_id: version,
          request_sha256: envelope.request_sha256,
          preflight_sha256: envelope.preflight_sha256,
          model_binding_sha256: "f".repeat(64),
          accepted: true,
          replayed: false,
          status: "queued",
        }),
      ),
    ),
  );
  await expect(submitSavedEngineRequest(envelope)).rejects.toThrow();
  expect(loadEngineSubmission(owner)).toEqual({ state: "pending", envelope });
});

it("parent navigation uses an exact version and rejects malformed links instead of becoming a new root", () => {
  expect(providerPreflightSearch({})).toEqual({});
  expect(
    providerPreflightSearch({ parent_factor_version_id: version }),
  ).toEqual({ parent_factor_version_id: version });
  expect(seededPreflightHref(version)).toBe(
    `/engine-runs/preflight?parent_factor_version_id=${version}`,
  );
  expect(() =>
    providerPreflightSearch({ parent_factor_version_id: "not-a-version" }),
  ).toThrow();
  expect(() => seededPreflightHref("javascript:bad")).toThrow();
});

it("provider failures only clear after explicitly documented post-key-lookup rejection", () => {
  const envelope = prepareEngineSubmission(owner, preflight);
  for (const code of [
    "preflight_changed",
    "engine_request_ineligible",
    "runtime_profile_changed",
  ])
    expect(definitelyNotAccepted({ status: 409, code }, envelope)).toBe(true);
  for (const code of [
    "rd_model_unavailable",
    "rd_provider_disabled",
    "steer_selection_changed",
    "engine_submission_not_enabled",
    "engine_runtime_unconfigured",
    "engine_submission_schema_unavailable",
    "neutral_busy",
    "idempotency_conflict",
  ])
    expect(definitelyNotAccepted({ status: 409, code }, envelope)).toBe(false);
});
