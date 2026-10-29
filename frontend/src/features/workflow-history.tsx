import type {
  AnyWorkflowView as WorkflowView,
  AnyWorkflowStageHistory as WorkflowStageHistory,
} from "@/lib/research-workflows";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ErrorState, Loading, Panel } from "@/components/common";
import {
  readWorkflow,
  readWorkflowSelection,
  readWorkflowStage,
  readWorkflowStages,
} from "@/lib/research-workflows";
import type { WorkflowInputSnapshot } from "@/workflow-contracts/types.gen";
import { WorkflowControls } from "./workflow-controls";
import { WorkflowSelectionEditor } from "./workflow-selection";
import {
  WorkflowStageNavigation,
  workflowStageTargets,
  stageSelectionFromHash,
  stageSelectionHash,
} from "./workflow-stage-navigation";
import { WorkflowDerivation } from "./workflow-derivation";
import { InputContentActions } from "./input-content";
import { ObservationSteer } from "./observation-steer";
import { deploymentUrl } from "@/lib/deployment";

export function WorkflowHistoryView({
  workflow,
  selection,
  stage,
}: {
  workflow: WorkflowView;
  selection?: WorkflowInputSnapshot;
  stage?: WorkflowStageHistory;
}) {
  return (
    <section aria-label="Actual workflow history">
      <h2>Workflow {workflow.workflow_id}</h2>
      <p>
        Current recorded state: {workflow.status}; revision {workflow.revision}.
      </p>
      {(workflow.status === "pause_requested" ||
        workflow.status === "cancel_requested") && (
        <p role="status">
          Pause or cancel is pending at the stage boundary. Any already admitted
          stage keeps its frozen input version and may still start from its
          queue; acknowledgement waits for verified physical stop and
          settlement. No subsequent stage is authorized by this pending request.
        </p>
      )}
      {workflow.blocked_reason && (
        <p role="status">Blocked: {workflow.blocked_reason}</p>
      )}
      {workflow.blocked_reason === "workflow_structured_release_required" && (
        <p role="status">
          Waiting for explicit release of the actual final RD snapshot and
          analysis. This plan does not auto-release outputs or invent an OE
          result. Open the recorded RD outputs to review and release them.
        </p>
      )}
      {stage?.kind === "structured_factor_execution" && (
        <p>
          Actual structured OE child stage, not a native Python checkpoint.
          Selected guidance, submitted request evidence, physical stop and
          settlement are separate observations.
        </p>
      )}
      {(workflow.blocked_reason === "workflow_child_failed_unverified" ||
        workflow.blocked_reason === "workflow_child_cancelled_unverified") && (
        <p>
          Child terminal status is recorded metadata only. Physical stop,
          settlement, and a safe checkpoint are not verified; no feedback
          release, retry, or next child is authorized.
        </p>
      )}
      <p>
        Saving manual guidance is not applying a selection. A later edit cannot
        change a running or completed stage.
      </p>
      <Panel title="Saved selection for a future stage">
        <p>
          Selected revision: {workflow.selected_input_revision_id}. This is not
          evidence of model consumption.
        </p>
        {selection && (
          <ol>
            {(selection.refs ?? []).map((ref) => (
              <li key={`${ref.origin}:${ref.version_id}`}>
                {ref.origin} · version {ref.version_id} · commitment{" "}
                {ref.content_sha256}
              </li>
            ))}
          </ol>
        )}
      </Panel>
      {!workflow.current_stage && (
        <p>No current stage recorded; no child launch is inferred.</p>
      )}
      {stage && (
        <Panel title={`Actual stage ${stage.ordinal}: ${stage.kind}`}>
          <p>
            Source receipt state: {stage.source_status}. Started:{" "}
            {stage.started_at ?? "not recorded"}; finished:{" "}
            {stage.finished_at ?? "not recorded"}.
          </p>
          {stage.source_status === "started_no_terminal_receipt" && (
            <p>
              Start recorded without a terminal receipt; not proof of a
              still-running process.
            </p>
          )}
          {stage.safe_error_code && (
            <p role="alert">Recorded error: {stage.safe_error_code}</p>
          )}
          <p>
            Frozen stage input revision: {stage.input_revision_id}; child run:{" "}
            {stage.child_run_id ?? "none recorded"}.
          </p>
          {selection && selection.revision_id !== stage.input_revision_id && (
            <p role="status">
              Saved future selection differs from this stage’s frozen selection.
              This stage continues to use its old version; no live edit
              occurred.
            </p>
          )}
          <h3>Frozen ordered stage inputs</h3>
          <ol>
            {(stage.input_refs ?? []).map((ref) => (
              <li key={`${ref.origin}:${ref.version_id}`}>
                {ref.origin} · {ref.version_id} · {ref.content_sha256}
              </li>
            ))}
          </ol>
          {(stage.automatic_feedback_refs ?? []).length > 0 && (
            <section aria-label="Automatically included experiment feedback">
              <h3>Automatically included experiment feedback</h3>
              <p>
                Derived from this workflow’s preceding released experiment
                feedback, not manual guidance or a user approval.
              </p>
              <p>
                Bound predecessor checkpoint: {stage.predecessor_checkpoint_id}.
              </p>
              {(stage.automatic_feedback_refs ?? []).map((ref) => (
                <div key={ref.version_id}>
                  <p>
                    {ref.origin} · {ref.version_id} · {ref.content_sha256}
                  </p>
                  <InputContentActions
                    reference={{
                      kind: "feedback",
                      id: ref.version_id,
                      sha256: ref.content_sha256,
                    }}
                  />
                </div>
              ))}
            </section>
          )}
          <h3>Actual model request evidence</h3>
          {!(stage.model_requests ?? []).length && (
            <p>
              No model request evidence is shown. Do not infer submission or
              compliance from selection.
            </p>
          )}
          {(stage.model_requests ?? []).map((request, index) => (
            <div key={`${request.model_operation_id ?? "none"}:${index}`}>
              <p>
                {request.state === "included_in_submitted_prompt"
                  ? "Provider response completed; guidance was included in this exact submitted request, not proof of compliance."
                  : request.state === "submission_uncertain"
                    ? "Submission attempted; delivery/response not confirmed."
                    : request.state === "selected_not_submitted"
                      ? "Prepared selection; not submitted."
                      : request.state === "not_consumed_numeric"
                        ? "Numeric stage does not consume guidance text."
                        : request.state === "not_submitted_to_model"
                          ? "Deterministic processing; not submitted to a model."
                          : "Model request consumption not observed; no submission or compliance claim."}
              </p>
              <p>
                Operation: {request.model_operation_id ?? "none"}; transport:{" "}
                {request.transport_id ?? "none"}; request commitment:{" "}
                {request.request_sha256 ?? "none"}.
              </p>
            </div>
          ))}
          <section aria-label="Recorded stage outputs">
            <h3>Recorded stage outputs</h3>
            <p>
              Exact retained identities, not a grant of result disclosure. The
              result page independently checks source availability and release.
            </p>
            {!(stage.output_refs ?? []).length && (
              <p>No retained output reference recorded for this stage.</p>
            )}
            {(stage.output_refs ?? []).map((output) => (
              <div key={`${output.output_kind}:${output.output_id}`}>
                <p>
                  {output.output_kind} · {output.output_id} ·{" "}
                  {output.content_sha256}
                </p>
                <a
                  href={deploymentUrl(
                    output.output_kind === "direct_analysis"
                      ? `/engine-runs/${output.child_run_id}/analyses/${output.output_id}`
                      : `/engine-runs/structured-search/runs/${output.child_run_id}?execution_id=${encodeURIComponent(output.output_id)}`,
                  )}
                >
                  Open exact {output.output_kind} {output.output_id}
                </a>
              </div>
            ))}
          </section>
          <h3>Actual derived input versions</h3>
          {(stage.derived_input_refs ?? []).map((ref) => (
            <div key={`${ref.origin}:${ref.version_id}`}>
              <p>
                {ref.origin}: {ref.version_id}
              </p>
              {(ref.origin === "normalized_external_proposal" ||
                ref.origin === "experiment_feedback_proposal") && (
                <InputContentActions
                  reference={{
                    kind:
                      ref.origin === "experiment_feedback_proposal"
                        ? "feedback"
                        : "normalized",
                    id: ref.version_id,
                    sha256: ref.content_sha256,
                  }}
                />
              )}
            </div>
          ))}
          <p>
            Containment: {stage.containment}; settlement: {stage.settlement}.
            These are separate from output success and guidance consumption.
          </p>
          <p>
            Checkpoint: {stage.checkpoint_id ?? "none recorded"}. Metadata does
            not grant result disclosure or child execution.
          </p>
        </Panel>
      )}
    </section>
  );
}

export function WorkflowHistory({
  id,
  ownerId,
  enabled = false,
}: {
  id: string;
  ownerId?: string;
  enabled?: boolean;
}) {
  const [selectedStage, setSelectedStage] = useState<string | undefined>(() =>
    typeof window === "undefined"
      ? undefined
      : stageSelectionFromHash(window.location.hash, id),
  );
  useEffect(() => {
    const restore = () =>
      setSelectedStage(stageSelectionFromHash(window.location.hash, id));
    restore();
    window.addEventListener("hashchange", restore);
    window.addEventListener("popstate", restore);
    return () => {
      window.removeEventListener("hashchange", restore);
      window.removeEventListener("popstate", restore);
    };
  }, [id, ownerId]);
  function selectStage(stageId?: string) {
    const url = new URL(window.location.href);
    url.hash = stageSelectionHash(url.hash, id, stageId);
    window.history.replaceState(window.history.state, "", url);
    setSelectedStage(stageId);
  }
  const [cursors, setCursors] = useState<number[]>([0]);
  const cursor = cursors[cursors.length - 1];
  const root = useQuery({
    queryKey: ["workflow", ownerId, id],
    enabled,
    queryFn: ({ signal }) => readWorkflow(id, signal),
    retry: false,
  });
  const selection = useQuery({
    queryKey: ["workflow", ownerId, id, "selection"],
    enabled,
    queryFn: ({ signal }) => readWorkflowSelection(id, signal),
    retry: false,
  });
  const { currentStageId, stageId } = workflowStageTargets(
    root.data?.current_stage?.stage_id,
    selectedStage,
  );
  const currentStage = useQuery({
    queryKey: ["workflow", ownerId, id, "stage", currentStageId],
    enabled: enabled && Boolean(currentStageId),
    queryFn: ({ signal }) => readWorkflowStage(id, currentStageId!, signal),
    retry: false,
  });
  const stages = useQuery({
    queryKey: ["workflow", ownerId, id, "stages", cursor],
    enabled,
    queryFn: ({ signal }) => readWorkflowStages(id, cursor, signal),
    retry: false,
  });
  const stage = useQuery({
    queryKey: ["workflow", ownerId, id, "stage", stageId],
    enabled: enabled && Boolean(stageId),
    queryFn: ({ signal }) => readWorkflowStage(id, stageId!, signal),
    retry: false,
  });
  const refreshHistory = () => {
    void root.refetch();
    void selection.refetch();
    void stages.refetch();
    if (currentStageId) void currentStage.refetch();
    if (stageId && stageId !== currentStageId) void stage.refetch();
  };
  if (!enabled)
    return (
      <p>Workflow history is not enabled by the installed server capability.</p>
    );
  if (root.isPending) return <Loading />;
  if (root.error)
    return <ErrorState error={root.error} retry={() => void root.refetch()} />;
  return (
    <>
      <button onClick={refreshHistory}>Refresh actual workflow history</button>
      <WorkflowStageNavigation
        page={stages.data}
        pending={stages.isFetching}
        currentStageId={currentStageId}
        selectedStageId={stageId}
        canPrevious={cursors.length > 1}
        onCurrent={() => selectStage(undefined)}
        onSelect={selectStage}
        onPrevious={() => setCursors((values) => values.slice(0, -1))}
        onNext={() => {
          const last = stages.data?.items.at(-1);
          if (stages.data?.has_more && last)
            setCursors((values) => [...values, last.ordinal]);
        }}
      />
      {stages.error && <ErrorState error={stages.error} />}
      {currentStage.error && <ErrorState error={currentStage.error} />}
      {stageId && stage.isPending && <Loading />}
      {selection.error && <ErrorState error={selection.error} />}
      {stage.error && <ErrorState error={stage.error} />}
      {ownerId && selection.data ? (
        <WorkflowSelectionEditor
          key={`${ownerId}:${id}:${selection.data.revision_id}`}
          ownerId={ownerId}
          workflow={root.data}
          selection={selection.data}
          stage={currentStage.data}
          refresh={refreshHistory}
        />
      ) : (
        ownerId && (
          <WorkflowControls
            key={`${ownerId}:${id}`}
            ownerId={ownerId}
            workflow={root.data}
            refresh={refreshHistory}
          />
        )
      )}
      <WorkflowHistoryView
        workflow={root.data}
        selection={selection.data}
        stage={stage.data}
      />
      {ownerId && stage.data && (
        <ObservationSteer
          key={`observation:${ownerId}:${id}:${stage.data.stage_id}`}
          observation={{
            kind: "workflow_stage_metadata",
            refs: {
              workflow_id: id,
              stage_id: stage.data.stage_id,
              input_revision_id: stage.data.input_revision_id,
              definition_sha256: stage.data.definition_sha256,
            },
          }}
        />
      )}
      {ownerId && stage.data && (
        <WorkflowDerivation
          key={`${ownerId}:${id}:${stage.data.stage_id}`}
          ownerId={ownerId}
          workflowId={id}
          stage={stage.data}
        />
      )}
    </>
  );
}
