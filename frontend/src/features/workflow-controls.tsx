import type { AnyWorkflowView as WorkflowView } from "@/lib/research-workflows";
import { useRef, useState } from "react";
import { ErrorState } from "@/components/common";
import {
  clearRejectedWorkflowMutation,
  loadWorkflowMutation,
  submitWorkflowMutation,
  type WorkflowMutation,
} from "@/lib/research-workflows";
import type { InputVersionRef } from "@/workflow-contracts/types.gen";

export function WorkflowControls({
  ownerId,
  workflow,
  futureRefs,
  refresh,
}: {
  ownerId: string;
  workflow: WorkflowView;
  futureRefs?: InputVersionRef[];
  refresh: () => void;
}) {
  const [saved, setSaved] = useState(() =>
    loadWorkflowMutation(ownerId, workflow.workflow_id),
  );
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState("");
  const lock = useRef(false);
  async function send(body: WorkflowMutation) {
    if (lock.current) return;
    lock.current = true;
    setPending(true);
    setError(null);
    setNotice("");
    try {
      const receipt = await submitWorkflowMutation(ownerId, body);
      setNotice(
        `Command recorded at revision ${receipt.revision}, status ${receipt.status}. This is not a physical-stop or model-consumption acknowledgement.`,
      );
      refresh();
    } catch (failure) {
      setError(failure);
    } finally {
      setSaved(loadWorkflowMutation(ownerId, workflow.workflow_id));
      lock.current = false;
      setPending(false);
    }
  }
  function control(action: "pause_after_stage" | "cancel" | "resume") {
    if (!workflow.controls[action] || saved.state !== "empty") return;
    void send({
      schema_version: "mineagent.workflow-command.v1",
      workflow_id: workflow.workflow_id,
      expected_revision: workflow.revision,
      idempotency_key: crypto.randomUUID(),
      action,
    });
  }
  const conflict =
    (error as { status?: number; code?: string } | null)?.status === 409 &&
    (error as { code?: string } | null)?.code === "workflow_revision_conflict";
  return (
    <section aria-label="Workflow future-stage controls">
      <p>
        Saving a steer version and selecting it for a future stage are separate
        writes. If a claim wins the race, the running stage keeps its frozen old
        input.
      </p>
      <p>
        Stage admission freezes its inputs. Pause or cancel requested before the
        next admission prevents that next stage; an already admitted stage,
        including one still queued, must finish its actual stop and settlement
        before the workflow acknowledges the request. This is not an immediate
        process-stop control.
      </p>
      {saved.state === "empty" ? (
        <>
          {(["pause_after_stage", "cancel", "resume"] as const).map(
            (action) =>
              workflow.controls[action] && (
                <button
                  key={action}
                  disabled={pending}
                  onClick={() => control(action)}
                >
                  {action.replaceAll("_", " ")}
                </button>
              ),
          )}
          {futureRefs && (
            <button
              disabled={
                pending ||
                ["cancelled", "completed", "cancel_requested"].includes(
                  workflow.status,
                )
              }
              onClick={() =>
                void send({
                  schema_version: "mineagent.workflow-select-inputs.v1",
                  workflow_id: workflow.workflow_id,
                  expected_revision: workflow.revision,
                  idempotency_key: crypto.randomUUID(),
                  refs: futureRefs,
                })
              }
            >
              Save this exact ordered selection for a future stage
            </button>
          )}
        </>
      ) : (
        <>
          <p>
            Unresolved command: editing/replacement is locked; exact replay
            retains its old revision and key.
          </p>
          <button
            disabled={pending || saved.state !== "pending"}
            onClick={() => {
              if (saved.state === "pending") void send(saved.body);
            }}
          >
            Retry exact command
          </button>
        </>
      )}
      {conflict && (
        <>
          <p role="status">
            The new selection/control was not applied: workflow revision
            changed. A separately saved steer remains saved, not consumed.
            Refresh actual stage receipts before selecting again.
          </p>
          <button
            disabled={pending}
            onClick={() => {
              clearRejectedWorkflowMutation(
                ownerId,
                workflow.workflow_id,
                error,
              );
              setSaved(loadWorkflowMutation(ownerId, workflow.workflow_id));
              setError(null);
              refresh();
            }}
          >
            Acknowledge CAS rejection and refresh — do not auto-reapply
          </button>
        </>
      )}
      {notice && <p role="status">{notice}</p>}
      {error !== null && <ErrorState error={error} />}
    </section>
  );
}
