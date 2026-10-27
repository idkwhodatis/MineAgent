/** Durable023 retry identity only. Backend remains authoritative for schema/admission.
 * A pending envelope is immutable; edits cannot silently replace an uncertain POST.
 */
import { z } from "zod";
import { APP_SUBPATH } from "./deployment";
import type { EnginePreflight, EngineRequest } from "./engine-runs-api";

export const receiptVersions = {
  "mineagent.rdagent-submission.v1": "mineagent.rdagent-submission-receipt.v1",
  "mineagent.rdagent-seeded-submission.v1":
    "mineagent.rdagent-seeded-submission-receipt.v1",
  "mineagent.rdagent-provider-submission.v1":
    "mineagent.rdagent-provider-submission-receipt.v1",
  "mineagent.rdagent-provider-seeded-submission.v1":
    "mineagent.rdagent-provider-seeded-submission-receipt.v1",
} as const;
const hash = z.string().regex(/^[a-f0-9]{64}$/);
const envelopeSchema = z.strictObject({
  version: z.literal(1),
  owner: z.uuid(),
  key: z.uuid(),
  request_sha256: hash,
  preflight_sha256: hash,
  body_json: z.string().min(2).max(131072),
  expected_model_binding_sha256: hash.optional(),
  expected_parent_binding_sha256: hash.optional(),
});
export type EngineSubmissionEnvelope = z.infer<typeof envelopeSchema>;
export type EngineSubmissionBody = EngineRequest & {
  preflight_sha256: string;
};
export type SavedEngineSubmission =
  | { state: "empty" }
  | { state: "invalid" }
  | { state: "pending"; envelope: EngineSubmissionEnvelope };
const storageKey = (owner: string) =>
  `mineagent.rd-submit.v1:${APP_SUBPATH}:${owner}`;

export function submissionBody(
  envelope: EngineSubmissionEnvelope,
): EngineSubmissionBody {
  const body = JSON.parse(envelope.body_json);
  if (
    !body ||
    Array.isArray(body) ||
    typeof body !== "object" ||
    typeof body.schema_version !== "string" ||
    !Object.hasOwn(receiptVersions, body.schema_version) ||
    body.purpose !== "factor_research" ||
    body.engine !== "rdagent" ||
    !z.uuid().safeParse(body.dataset_version_id).success ||
    body.preflight_sha256 !== envelope.preflight_sha256
  )
    throw new Error(
      "Saved submission identity is malformed; do not create a replacement automatically.",
    );
  const seeded = body.schema_version.includes("seeded");
  const provider = body.schema_version.includes("provider");
  if (
    (provider &&
      !hash.safeParse(envelope.expected_model_binding_sha256).success) ||
    (seeded && !hash.safeParse(envelope.expected_parent_binding_sha256).success)
  )
    throw new Error(
      "Saved model or parent commitment is missing; retain the unresolved request.",
    );
  if (
    (seeded && !z.uuid().safeParse(body.parent_factor_version_id).success) ||
    (!seeded && "parent_factor_version_id" in body) ||
    (provider &&
      (!/^[a-z][a-z0-9_-]{0,63}$/.test(body.model_profile_id ?? "") ||
        !Array.isArray(body.selected_steer_version_ids) ||
        body.selected_steer_version_ids.length > 8 ||
        new Set(body.selected_steer_version_ids).size !==
          body.selected_steer_version_ids.length ||
        body.selected_steer_version_ids.some(
          (id: unknown) => !z.uuid().safeParse(id).success,
        ))) ||
    (!provider &&
      ("selected_steer_version_ids" in body || "model_profile_id" in body))
  )
    throw new Error(
      "Saved submission family or selected version identity differs; keep the original retry record.",
    );
  // Never interpret this local shape check as server acceptance or a runtime grant.
  return body as EngineSubmissionBody;
}
export function prepareEngineSubmission(
  owner: string,
  preflight: EnginePreflight,
): EngineSubmissionEnvelope {
  if (!preflight.can_submit || !preflight.data_eligible)
    throw new Error("Server preflight does not permit submission.");
  if ("steer_snapshot" in preflight) {
    if (
      !preflight.model_binding ||
      preflight.model_binding.profile_id !==
        preflight.normalized_request.model_profile_id ||
      !hash.safeParse(preflight.model_binding_sha256).success
    )
      throw new Error(
        "Preflight model binding is absent or differs from the chosen model.",
      );
    const selected =
      preflight.normalized_request.selected_steer_version_ids ?? [];
    if (
      preflight.steer_snapshot.owner_id !== owner ||
      JSON.stringify(selected) !==
        JSON.stringify(
          (preflight.steer_snapshot.versions ?? []).map(
            (version) => version.version_id,
          ),
        )
    )
      throw new Error(
        "Preflight steer snapshot does not match this owner and ordered selection.",
      );
  }
  const envelope = envelopeSchema.parse({
    version: 1,
    owner,
    key: crypto.randomUUID(),
    request_sha256: preflight.request_sha256,
    preflight_sha256: preflight.preflight_sha256,
    ...("model_binding_sha256" in preflight
      ? { expected_model_binding_sha256: preflight.model_binding_sha256 }
      : {}),
    ...("parent_factor_version_id" in preflight.normalized_request &&
    "parent_binding_sha256" in preflight
      ? { expected_parent_binding_sha256: preflight.parent_binding_sha256 }
      : {}),
    body_json: JSON.stringify({
      ...preflight.normalized_request,
      preflight_sha256: preflight.preflight_sha256,
    }),
  });
  submissionBody(envelope);
  return envelope;
}
export function loadEngineSubmission(owner: string): SavedEngineSubmission {
  try {
    const raw = sessionStorage.getItem(storageKey(owner));
    if (raw === null) return { state: "empty" };
    if (raw.length > 150000) return { state: "invalid" };
    const parsed = envelopeSchema.safeParse(JSON.parse(raw));
    if (!parsed.success || parsed.data.owner !== owner)
      return { state: "invalid" };
    submissionBody(parsed.data);
    return { state: "pending", envelope: parsed.data };
  } catch {
    return { state: "invalid" };
  }
}
export function saveEngineSubmission(
  envelope: EngineSubmissionEnvelope,
): boolean {
  try {
    envelopeSchema.parse(envelope);
    submissionBody(envelope);
    const current = loadEngineSubmission(envelope.owner);
    if (
      current.state === "invalid" ||
      (current.state === "pending" &&
        JSON.stringify(current.envelope) !== JSON.stringify(envelope))
    )
      return false;
    sessionStorage.setItem(
      storageKey(envelope.owner),
      JSON.stringify(envelope),
    );
    return true;
  } catch {
    return false;
  }
}
export function clearEngineSubmission(
  envelope: EngineSubmissionEnvelope,
): boolean {
  try {
    const current = loadEngineSubmission(envelope.owner);
    if (
      current.state !== "pending" ||
      JSON.stringify(current.envelope) !== JSON.stringify(envelope)
    )
      return false;
    sessionStorage.removeItem(storageKey(envelope.owner));
    return true;
  } catch {
    return false;
  }
}
const notAcceptedCodes = new Set([
  "preflight_changed",
  "engine_request_ineligible",
  "run_quota",
  "engine_submission_not_enabled",
  "engine_runtime_unconfigured",
  "engine_runtime_unsupported",
  "runtime_profile_changed",
]);
/** Only explicit post-owner-key-lookup rejections are safe to edit/re-preflight. */
export function definitelyNotAccepted(
  error: unknown,
  envelope?: EngineSubmissionEnvelope,
): boolean {
  if (
    !error ||
    typeof error !== "object" ||
    !("status" in error) ||
    !("code" in error)
  )
    return false;
  let codes = notAcceptedCodes;
  if (envelope) {
    try {
      if (submissionBody(envelope).schema_version?.includes("provider"))
        codes = new Set([
          "preflight_changed",
          "engine_request_ineligible",
          "runtime_profile_changed",
        ]);
    } catch {
      return false;
    }
  }
  return (
    [400, 409, 422, 429].includes(Number(error.status)) &&
    typeof error.code === "string" &&
    codes.has(error.code)
  );
}
