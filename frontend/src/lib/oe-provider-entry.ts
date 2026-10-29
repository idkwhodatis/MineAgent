import { z } from "zod";
import { queryOptions } from "@tanstack/react-query";
import { APP_SUBPATH } from "./deployment";
import {
  oeModels,
  oeSnapshots,
  oePreflight,
  oeSubmit,
} from "./oe-provider-api";
import type {
  ProviderStructuredPreflight,
  ProviderStructuredSearchRequest,
  ProviderStructuredSearchSubmit,
} from "@/client/types.gen";
const sha = z.string().regex(/^[a-f0-9]{64}$/);
const envelopeSchema = z.strictObject({
  version: z.literal(1),
  owner: z.uuid(),
  key: z.uuid(),
  request_sha256: sha,
  preflight_sha256: sha,
  body_json: z.string().min(2).max(65536),
});
export type OEPendingSubmission = z.infer<typeof envelopeSchema>;
export type SavedOESubmission =
  | { state: "empty" }
  | { state: "invalid" }
  | { state: "pending"; envelope: OEPendingSubmission };
const receiptSchema = z.strictObject({
  schema_version: z.literal(
    "mineagent.oe-structured-provider-submission-receipt.v1",
  ),
  id: z.uuid(),
  run_id: z.uuid(),
  origin: z.literal("authenticated_user_submission"),
  origin_user_id: z.uuid(),
  request_sha256: sha,
  preflight_sha256: sha,
  normalized_request: z.record(z.string(), z.unknown()),
  required_process_units: z.union([z.literal(4), z.literal(6)]),
});
export type OEAcceptedReceipt = z.infer<typeof receiptSchema>;
const storageKey = (owner: string) =>
  `mineagent.oe-provider-submit.v1:${APP_SUBPATH}:${owner}`;
export const oeModelsOptions = (owner: string) =>
  queryOptions({
    queryKey: ["oe-provider-models", owner],
    queryFn: ({ signal }) => oeModels(signal),
    retry: false,
  });
export const oeSnapshotsOptions = (owner: string, offset: number) =>
  queryOptions({
    queryKey: ["oe-provider-snapshots", owner, offset],
    queryFn: ({ signal }) => oeSnapshots(offset, signal),
    retry: false,
  });
export const checkOESearch = (body: ProviderStructuredSearchRequest) =>
  oePreflight(body);
function stable(value: unknown): string {
  return JSON.stringify(value, (_key, item) =>
    item && typeof item === "object" && !Array.isArray(item)
      ? Object.fromEntries(
          Object.keys(item)
            .sort()
            .map((key) => [key, item[key]]),
        )
      : item,
  );
}
export function sameOERequest(a: unknown, b: unknown) {
  return stable(a) === stable(b);
}
export function oeSubmissionBody(
  envelope: OEPendingSubmission,
): ProviderStructuredSearchSubmit {
  envelopeSchema.parse(envelope);
  const body = JSON.parse(envelope.body_json);
  if (
    !body ||
    body.schema_version !== "mineagent.oe-structured-provider-submission.v1" ||
    body.profile !== "fixed_factor_operator_closed_program_v1" ||
    body.engine !== "openevolve" ||
    body.purpose !== "strategy_search" ||
    !z.uuid().safeParse(body.snapshot_id).success ||
    !sha.safeParse(body.model_binding_sha256).success ||
    !/^[a-z][a-z0-9_-]{0,63}$/.test(body.model_profile_id) ||
    body.preflight_sha256 !== envelope.preflight_sha256
  )
    throw new Error(
      "Saved OE request is malformed; do not replace an uncertain submission.",
    );
  return body;
}
export function prepareOESubmission(
  owner: string,
  result: ProviderStructuredPreflight,
): OEPendingSubmission {
  if (
    !result.can_submit ||
    !result.data_eligible ||
    result.reasons.length ||
    !result.fixed_input_sha256 ||
    !result.runtime_profile_sha256 ||
    !result.required_budget ||
    !result.model_binding ||
    result.model_binding.profile_id !==
      result.normalized_request.model_profile_id
  )
    throw new Error("Exact eligible operator preflight is required.");
  const envelope = envelopeSchema.parse({
    version: 1,
    owner,
    key: crypto.randomUUID(),
    request_sha256: result.request_sha256,
    preflight_sha256: result.preflight_sha256,
    body_json: JSON.stringify({
      ...result.normalized_request,
      preflight_sha256: result.preflight_sha256,
    }),
  });
  oeSubmissionBody(envelope);
  return envelope;
}
export function loadOESubmission(owner: string): SavedOESubmission {
  try {
    const raw = sessionStorage.getItem(storageKey(owner));
    if (raw === null) return { state: "empty" };
    if (raw.length > 70000) return { state: "invalid" };
    const envelope = envelopeSchema.parse(JSON.parse(raw));
    if (envelope.owner !== owner) return { state: "invalid" };
    oeSubmissionBody(envelope);
    return { state: "pending", envelope };
  } catch {
    return { state: "invalid" };
  }
}
export function saveOESubmission(envelope: OEPendingSubmission): boolean {
  try {
    oeSubmissionBody(envelope);
    const old = loadOESubmission(envelope.owner);
    if (
      old.state === "invalid" ||
      (old.state === "pending" && !sameOERequest(old.envelope, envelope))
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
export function clearOESubmission(envelope: OEPendingSubmission): boolean {
  try {
    const old = loadOESubmission(envelope.owner);
    if (old.state !== "pending" || !sameOERequest(old.envelope, envelope))
      return false;
    sessionStorage.removeItem(storageKey(envelope.owner));
    return true;
  } catch {
    return false;
  }
}
export function oeDefinitelyNotAccepted(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const value = error as { status?: unknown; code?: unknown };
  return (
    (value.status === 409 && value.code === "preflight_changed") ||
    (value.status === 429 && value.code === "run_quota")
  );
}
export async function submitSavedOE(envelope: OEPendingSubmission) {
  const body = oeSubmissionBody(envelope);
  const response = receiptSchema.parse(await oeSubmit(body, envelope.key));
  const { preflight_sha256: _preflight, ...expected } = body;
  if (
    response.origin_user_id !== envelope.owner ||
    response.request_sha256 !== envelope.request_sha256 ||
    response.preflight_sha256 !== envelope.preflight_sha256 ||
    !sameOERequest(response.normalized_request, expected) ||
    response.required_process_units !== 2 + 2 * body.maximum_mutation_slots
  )
    throw new Error(
      "OE receipt differs from the saved owner/request identity; retain the original retry envelope.",
    );
  return response;
}
const acceptedKey = (owner: string) =>
  `mineagent.oe-provider-last-receipt.v1:${APP_SUBPATH}:${owner}`;
export function saveOEReceipt(
  owner: string,
  value: OEAcceptedReceipt,
): boolean {
  try {
    const exact = receiptSchema.parse(value);
    if (exact.origin_user_id !== owner) return false;
    sessionStorage.setItem(acceptedKey(owner), JSON.stringify(exact));
    return true;
  } catch {
    return false;
  }
}
export function loadOEReceipt(owner: string): OEAcceptedReceipt | null {
  try {
    const raw = sessionStorage.getItem(acceptedKey(owner));
    if (raw === null || raw.length > 70000) return null;
    const value = receiptSchema.parse(JSON.parse(raw));
    return value.origin_user_id === owner ? value : null;
  } catch {
    return null;
  }
}
export function clearOEReceipt(owner: string, receiptId: string): boolean {
  try {
    if (loadOEReceipt(owner)?.id !== receiptId) return false;
    sessionStorage.removeItem(acceptedKey(owner));
    return true;
  } catch {
    return false;
  }
}
