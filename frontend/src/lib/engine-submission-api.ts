import { z } from "zod";
import { factorRequest } from "./factor-api";
import {
  submissionBody,
  receiptVersions,
  type EngineSubmissionEnvelope,
} from "./engine-submission-draft";
// Mirrors the authoritative023 receipt, not an optimistic client lifecycle.
const status = z.enum([
  "queued",
  "preparing",
  "running",
  "finalizing",
  "pause_requested",
  "paused",
  "cancel_requested",
  "cancelled",
  "failed",
  "interrupted",
  "succeeded",
]);
const sha = z.string().regex(/^[a-f0-9]{64}$/);
const commonReceipt = {
  run_id: z.uuid(),
  plan_id: z.uuid(),
  request_sha256: sha,
  preflight_sha256: sha,
  accepted: z.literal(true),
  replayed: z.boolean(),
  status,
};
const parentReceipt = {
  parent_factor_id: z.uuid(),
  parent_factor_version_id: z.uuid(),
  parent_binding_sha256: sha,
};
const submissionReceipt = z.discriminatedUnion("schema_version", [
  z.strictObject({
    ...commonReceipt,
    schema_version: z.literal(
      receiptVersions["mineagent.rdagent-submission.v1"],
    ),
  }),
  z.strictObject({
    ...commonReceipt,
    ...parentReceipt,
    schema_version: z.literal(
      receiptVersions["mineagent.rdagent-seeded-submission.v1"],
    ),
  }),
  z.strictObject({
    ...commonReceipt,
    model_binding_sha256: sha,
    schema_version: z.literal(
      receiptVersions["mineagent.rdagent-provider-submission.v1"],
    ),
  }),
  z.strictObject({
    ...commonReceipt,
    ...parentReceipt,
    model_binding_sha256: sha,
    schema_version: z.literal(
      receiptVersions["mineagent.rdagent-provider-seeded-submission.v1"],
    ),
  }),
]);
const controlReceipt = z.strictObject({
  schema_version: z.literal("mineagent.rdagent-control-receipt.v1"),
  run_id: z.uuid(),
  action: z.literal("cancel"),
  status,
  pending: z.boolean(),
});
export type EngineControlReceipt = z.infer<typeof controlReceipt>;
export async function submitSavedEngineRequest(
  envelope: EngineSubmissionEnvelope,
) {
  const body = submissionBody(envelope);
  const receipt = submissionReceipt.parse(
    await factorRequest<unknown>("/engine-runs", {
      method: "POST",
      body,
      key: envelope.key,
    }),
  );
  if (
    receipt.schema_version !== receiptVersions[body.schema_version!] ||
    ("parent_factor_version_id" in body &&
      (!("parent_factor_version_id" in receipt) ||
        receipt.parent_factor_version_id !== body.parent_factor_version_id)) ||
    (envelope.expected_model_binding_sha256 !== undefined &&
      (!("model_binding_sha256" in receipt) ||
        receipt.model_binding_sha256 !==
          envelope.expected_model_binding_sha256)) ||
    (envelope.expected_parent_binding_sha256 !== undefined &&
      (!("parent_binding_sha256" in receipt) ||
        receipt.parent_binding_sha256 !==
          envelope.expected_parent_binding_sha256)) ||
    receipt.request_sha256 !== envelope.request_sha256 ||
    receipt.preflight_sha256 !== envelope.preflight_sha256
  )
    throw new Error(
      "Submission reply does not match the saved request. Keep the original retry identity.",
    );
  return receipt;
}
export async function cancelEngineRun(runId: string) {
  const receipt = controlReceipt.parse(
    await factorRequest<unknown>(
      `/engine-runs/${encodeURIComponent(runId)}/cancel`,
      { method: "POST" },
    ),
  );
  if (
    receipt.run_id !== runId ||
    receipt.pending !== (receipt.status === "cancel_requested")
  )
    throw new Error(
      "Cancellation reply does not match this run; refresh authoritative status.",
    );
  return receipt;
}
export function engineRunActive(status: string) {
  return [
    "queued",
    "preparing",
    "running",
    "finalizing",
    "cancel_requested",
    "pause_requested",
  ].includes(status);
}
