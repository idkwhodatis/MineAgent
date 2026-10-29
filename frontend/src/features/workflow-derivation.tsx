import type { AnyWorkflowStageHistory as WorkflowStageHistory } from "@/lib/research-workflows";
import { useQuery } from "@tanstack/react-query";
import { ErrorState, Loading } from "@/components/common";
import { factorRequest } from "@/lib/factor-api";
import type { InputDerivationView } from "@/input-audit-contracts/types.gen";

import { InputDerivationAudit } from "./input-derivation-audit";

type DerivationOperation = InputDerivationView["operation"];
export function derivationOperation(
  kind: WorkflowStageHistory["kind"],
): DerivationOperation | undefined {
  return kind === "external_normalization" ||
    kind === "experiment_feedback_projection"
    ? kind
    : undefined;
}

export async function readWorkflowDerivation(
  workflowId: string,
  stageId: string,
  operation: DerivationOperation,
  signal?: AbortSignal,
): Promise<InputDerivationView | null> {
  let result: InputDerivationView;
  try {
    result = await factorRequest<InputDerivationView>(
      `/research-workflows/${encodeURIComponent(workflowId)}/stages/${encodeURIComponent(stageId)}/input-derivation`,
      { signal },
    );
  } catch (failure) {
    if ((failure as { status?: number } | null)?.status === 404) return null;
    throw failure;
  }
  if (
    result.workflow_id !== workflowId ||
    result.stage_id !== stageId ||
    result.operation !== operation ||
    result.qualification !==
      "receipt_metadata_not_live_status_or_content_approval"
  ) {
    throw new Error(
      "Input derivation receipt scope differs from the selected stage.",
    );
  }
  return result;
}

/** Metadata fetch only. Retained bytes are fetched solely by explicit content actions. */
export function WorkflowDerivation({
  ownerId,
  workflowId,
  stage,
}: {
  ownerId: string;
  workflowId: string;
  stage: Pick<WorkflowStageHistory, "stage_id" | "kind">;
}) {
  const operation = derivationOperation(stage.kind);
  const receipt = useQuery({
    queryKey: [
      "workflow",
      ownerId,
      workflowId,
      "stage",
      stage.stage_id,
      "derivation",
      operation,
    ],
    enabled: Boolean(operation),
    queryFn: ({ signal }) =>
      readWorkflowDerivation(workflowId, stage.stage_id, operation!, signal),
    retry: false,
  });
  if (!operation) return null;
  if (receipt.isPending) return <Loading />;
  if (receipt.error)
    return (
      <ErrorState error={receipt.error} retry={() => void receipt.refetch()} />
    );
  return (
    <section aria-label="Selected stage input and output receipts">
      <button onClick={() => void receipt.refetch()}>
        Refresh selected stage derivation receipt
      </button>
      {receipt.data ? (
        <InputDerivationAudit value={receipt.data} />
      ) : (
        <p>
          No derivation receipt is available for this stage. No start, output or
          completion is inferred.
        </p>
      )}
    </section>
  );
}
