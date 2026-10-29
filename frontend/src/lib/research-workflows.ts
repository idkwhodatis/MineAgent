import { assertStructuredWorkflowDeclaration } from "./workflow-structured";
/** Exact retry envelopes are display/client safety, never workflow authority. */
import { z } from "zod";
import { APP_SUBPATH } from "./deployment";
import { factorRequest } from "./factor-api";
import { readSession } from "./session";
import type {
  StructuredWorkflowRequest,
  StructuredWorkflowPreflight,
  StructuredWorkflowView,
  WorkflowOeStageHistory,
  WorkflowRequest,
  WorkflowPreflight,
  WorkflowCreated,
  WorkflowView,
  WorkflowStageHistory,
  WorkflowStageList,
  WorkflowInputSnapshot,
  WorkflowManualOptions,
  WorkflowCommand,
  WorkflowCommandReceipt,
  SelectWorkflowInputs,
} from "@/workflow-contracts/types.gen";
export type {
  WorkflowRequest,
  WorkflowPreflight,
  WorkflowView,
  WorkflowStageHistory,
  WorkflowInputSnapshot,
  WorkflowManualOptions,
};
export type AnyWorkflowRequest = WorkflowRequest | StructuredWorkflowRequest;
export type AnyWorkflowPreflight =
  WorkflowPreflight | StructuredWorkflowPreflight;
export type AnyWorkflowView = WorkflowView | StructuredWorkflowView;
export type AnyWorkflowStageHistory =
  WorkflowStageHistory | WorkflowOeStageHistory;
/** Route is derived only from the retained exact version, never current form mode. */
export function workflowSubmissionPath(body: {
  schema_version?: string;
  request?: { schema_version?: string };
}) {
  if (
    body.schema_version === "mineagent.workflow-public-submit.v1" &&
    body.request?.schema_version === "mineagent.workflow-public-request.v1"
  )
    return "/research-workflows";
  if (
    body.schema_version === "mineagent.workflow-public-submit.v2" &&
    body.request?.schema_version === "mineagent.workflow-public-request.v2"
  )
    return "/research-workflows/structured";
  throw new Error(
    "Workflow request version pair is invalid; retain the original request.",
  );
}
const envelopeSchema = z.strictObject({
  owner: z.uuid(),
  key: z.uuid(),
  body_json: z.string().min(2).max(135168),
});
export type WorkflowEnvelope = z.infer<typeof envelopeSchema>;
const storageKey = (owner: string) =>
  `mineagent.workflow-submit.v1:${APP_SUBPATH}:${owner}`;
export function loadWorkflowEnvelope(
  owner: string,
):
  | { state: "empty" }
  | { state: "invalid" }
  | { state: "pending"; envelope: WorkflowEnvelope } {
  try {
    const raw = sessionStorage.getItem(storageKey(owner));
    if (raw === null) return { state: "empty" };
    const envelope = envelopeSchema.parse(JSON.parse(raw));
    const body = JSON.parse(envelope.body_json);
    if (
      envelope.owner !== owner ||
      body.idempotency_key !== envelope.key ||
      !workflowSubmissionPath(body) ||
      !/^[a-f0-9]{64}$/.test(body.preflight_sha256)
    )
      return { state: "invalid" };
    return { state: "pending", envelope };
  } catch {
    return { state: "invalid" };
  }
}
export function prepareWorkflowEnvelope(
  owner: string,
  request: AnyWorkflowRequest,
  preflight: AnyWorkflowPreflight,
): WorkflowEnvelope {
  const v2 = request.schema_version === "mineagent.workflow-public-request.v2";
  if (
    preflight.schema_version !==
      (v2
        ? "mineagent.workflow-public-preflight.v2"
        : "mineagent.workflow-public-preflight.v1") ||
    preflight.can_create !== true
  )
    throw new Error("Workflow preflight version differs from request.");
  const key = crypto.randomUUID();
  const body = {
    schema_version: v2
      ? "mineagent.workflow-public-submit.v2"
      : "mineagent.workflow-public-submit.v1",
    idempotency_key: key,
    request,
    preflight_sha256: preflight.preflight_sha256,
  };
  return envelopeSchema.parse({ owner, key, body_json: JSON.stringify(body) });
}
export function saveWorkflowEnvelope(envelope: WorkflowEnvelope) {
  const existing = loadWorkflowEnvelope(envelope.owner);
  if (
    existing.state === "invalid" ||
    (existing.state === "pending" &&
      JSON.stringify(existing.envelope) !== JSON.stringify(envelope))
  )
    throw new Error(
      "An unresolved workflow request already exists; retain its original key and body.",
    );
  sessionStorage.setItem(storageKey(envelope.owner), JSON.stringify(envelope));
  const saved = loadWorkflowEnvelope(envelope.owner);
  if (
    saved.state !== "pending" ||
    saved.envelope.body_json !== envelope.body_json
  )
    throw new Error(
      "Exact retry request could not be retained; nothing was submitted.",
    );
}
export async function submitWorkflowEnvelope(envelope: WorkflowEnvelope) {
  saveWorkflowEnvelope(envelope);
  const session = readSession();
  if (!session) throw new Error("Sign in before submitting a workflow.");
  const owner = await factorRequest<{ id: string }>("/users/me");
  if (
    owner.id !== envelope.owner ||
    readSession()?.accessToken !== session.accessToken
  )
    throw new Error(
      "Signed-in owner changed; original workflow request was not submitted.",
    );
  const receipt = await factorRequest<WorkflowCreated>(
    workflowSubmissionPath(JSON.parse(envelope.body_json)),
    {
      method: "POST",
      body: JSON.parse(envelope.body_json),
    },
  );
  if (readSession()?.accessToken !== session.accessToken)
    throw new Error("Session changed; retain the original unresolved request.");
  if (
    receipt.schema_version !== "mineagent.workflow-created.v1" ||
    !z.uuid().safeParse(receipt.workflow_id).success ||
    receipt.revision !== 1 ||
    !["ready", "blocked"].includes(receipt.status)
  )
    throw new Error("Receipt identity is invalid; retain the exact request.");
  // Original creation receipt is not a current workflow status or child launch.
  sessionStorage.removeItem(storageKey(envelope.owner));
  return receipt;
}
export const preflightWorkflow = (request: AnyWorkflowRequest) => {
  if (request.schema_version === "mineagent.workflow-public-request.v2")
    assertStructuredWorkflowDeclaration(request);
  return factorRequest<AnyWorkflowPreflight>(
    request.schema_version === "mineagent.workflow-public-request.v2"
      ? "/research-workflows/structured/preflight"
      : "/research-workflows/preflight",
    {
      method: "POST",
      body: request,
    },
  );
};
export const readWorkflow = (id: string, signal?: AbortSignal) =>
  factorRequest<AnyWorkflowView>(
    `/research-workflows/${encodeURIComponent(id)}`,
    {
      signal,
    },
  );
export const readWorkflowStage = (
  id: string,
  stage: string,
  signal?: AbortSignal,
) =>
  factorRequest<AnyWorkflowStageHistory>(
    `/research-workflows/${encodeURIComponent(id)}/stages/${encodeURIComponent(stage)}`,
    { signal },
  );
export const readWorkflowSelection = (id: string, signal?: AbortSignal) =>
  factorRequest<WorkflowInputSnapshot>(
    `/research-workflows/${encodeURIComponent(id)}/selected-inputs`,
    { signal },
  );
export const readWorkflowManualOptions = (offset = 0, signal?: AbortSignal) =>
  factorRequest<WorkflowManualOptions>(
    `/research-workflow-inputs/manual?offset=${offset}&limit=100`,
    { signal },
  );
export const sendWorkflowCommand = (
  body: WorkflowCommand | SelectWorkflowInputs,
) =>
  factorRequest<WorkflowCommandReceipt>(
    `/research-workflows/${encodeURIComponent(body.workflow_id)}/${"action" in body ? "commands" : "selected-inputs"}`,
    { method: "POST", body },
  );

export type WorkflowMutation = WorkflowCommand | SelectWorkflowInputs;
const commandKey = (owner: string, id: string) =>
  `mineagent.workflow-command.v1:${APP_SUBPATH}:${owner}:${id}`;
export function loadWorkflowMutation(
  owner: string,
  id: string,
):
  | { state: "empty" }
  | { state: "invalid" }
  | { state: "pending"; body: WorkflowMutation } {
  try {
    const raw = sessionStorage.getItem(commandKey(owner, id));
    if (raw === null) return { state: "empty" };
    const body = JSON.parse(raw);
    if (
      body.workflow_id !== id ||
      !z.uuid().safeParse(body.idempotency_key).success ||
      !Number.isSafeInteger(body.expected_revision) ||
      body.expected_revision < 1 ||
      ![
        "mineagent.workflow-command.v1",
        "mineagent.workflow-select-inputs.v1",
      ].includes(body.schema_version)
    )
      return { state: "invalid" };
    return { state: "pending", body };
  } catch {
    return { state: "invalid" };
  }
}
export function clearRejectedWorkflowMutation(
  owner: string,
  id: string,
  failure: unknown,
) {
  // Both authoritative services perform exact-key replay before this CAS check.
  const error = failure as { status?: number; code?: string } | null;
  if (error?.status !== 409 || error.code !== "workflow_revision_conflict")
    throw new Error(
      "The command outcome is unresolved; retain the exact key/body.",
    );
  sessionStorage.removeItem(commandKey(owner, id));
}
export async function submitWorkflowMutation(
  owner: string,
  body: WorkflowMutation,
) {
  const saved = loadWorkflowMutation(owner, body.workflow_id);
  if (
    saved.state === "invalid" ||
    (saved.state === "pending" &&
      JSON.stringify(saved.body) !== JSON.stringify(body))
  )
    throw new Error("A different workflow command is unresolved.");
  sessionStorage.setItem(
    commandKey(owner, body.workflow_id),
    JSON.stringify(body),
  );
  const verified = loadWorkflowMutation(owner, body.workflow_id);
  if (
    verified.state !== "pending" ||
    JSON.stringify(verified.body) !== JSON.stringify(body)
  )
    throw new Error("Exact command could not be saved; nothing was sent.");
  const session = readSession();
  const actual = await factorRequest<{ id: string }>("/users/me");
  if (
    !session ||
    actual.id !== owner ||
    readSession()?.accessToken !== session.accessToken
  )
    throw new Error("Signed-in owner changed; original command was not sent.");
  const receipt = await sendWorkflowCommand(body);
  if (
    readSession()?.accessToken !== session.accessToken ||
    receipt.workflow_id !== body.workflow_id ||
    receipt.schema_version !== "mineagent.workflow-command-receipt.v1"
  )
    throw new Error(
      "Command acknowledgement differs; preserve the unresolved request.",
    );
  sessionStorage.removeItem(commandKey(owner, body.workflow_id));
  return receipt;
}

export const readWorkflowStages = (
  id: string,
  afterOrdinal = 0,
  signal?: AbortSignal,
) =>
  factorRequest<WorkflowStageList>(
    `/research-workflows/${encodeURIComponent(id)}/stages?after_ordinal=${afterOrdinal}&limit=32`,
    { signal },
  );
