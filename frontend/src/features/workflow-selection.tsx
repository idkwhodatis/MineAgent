import type {
  AnyWorkflowView as WorkflowView,
  AnyWorkflowStageHistory as WorkflowStageHistory,
} from "@/lib/research-workflows";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ErrorState } from "@/components/common";
import { deploymentUrl } from "@/lib/deployment";
import { readWorkflowManualOptions } from "@/lib/research-workflows";
import type {
  InputVersionRef,
  WorkflowInputSnapshot,
} from "@/workflow-contracts/types.gen";
import { WorkflowControls } from "./workflow-controls";
import { InputContentActions } from "./input-content";

export function replaceWorkflowInput(
  refs: InputVersionRef[],
  next: InputVersionRef,
) {
  if (next.origin === "unknown")
    throw new Error("Unknown origin cannot be selected.");
  const found = refs.findIndex(
    (ref) => ref.origin === next.origin && ref.record_id === next.record_id,
  );
  if (found < 0) {
    if (refs.length >= 32)
      throw new Error("At most 32 exact input versions; no truncation.");
    return [...refs, next];
  }
  return refs.map((ref, index) => (index === found ? next : ref));
}

/** Mount keyed by actual selected revision: refresh never auto-applies a local edit. */
export function WorkflowSelectionEditor({
  ownerId,
  workflow,
  selection,
  stage,
  refresh,
}: {
  ownerId: string;
  workflow: WorkflowView;
  selection: WorkflowInputSnapshot;
  stage?: WorkflowStageHistory;
  refresh: () => void;
}) {
  const [refs, setRefs] = useState<InputVersionRef[]>(selection.refs ?? []);
  const [error, setError] = useState<unknown>(null);
  const options = useQuery({
    queryKey: ["workflow-manual-inputs", ownerId],
    queryFn: ({ signal }) => readWorkflowManualOptions(0, signal),
    retry: false,
  });
  const changed = JSON.stringify(refs) !== JSON.stringify(selection.refs ?? []);
  function select(ref: InputVersionRef) {
    try {
      setRefs(replaceWorkflowInput(refs, ref));
      setError(null);
    } catch (failure) {
      setError(failure);
    }
  }
  if (
    selection.owner_id !== ownerId ||
    selection.workflow_id !== workflow.workflow_id ||
    selection.revision_id !== workflow.selected_input_revision_id
  )
    return (
      <p role="alert">
        Selection identity/revision changed; refresh before editing.
      </p>
    );
  return (
    <section aria-label="Edit future workflow selection">
      <h3>Future-stage inputs, separate from saved manual versions</h3>
      <a href={deploymentUrl("/rd-steers")}>Create or edit manual guidance</a>
      <p>
        Return and refresh available versions, then explicitly choose and save a
        selection. A CRUD save alone is not applied to this workflow.
      </p>
      <button onClick={() => void options.refetch()}>
        Refresh available manual versions
      </button>
      <h4>User-provided manual guidance — pinned first</h4>
      {options.error && <ErrorState error={options.error} />}
      {options.data?.items.map((option) => (
        <div key={option.ref.version_id}>
          <p>
            {option.version.kind}: {option.version.text} · version{" "}
            {option.version.ordinal}
          </p>
          <button
            disabled={
              option.disabled ||
              refs.some((ref) => ref.version_id === option.ref.version_id)
            }
            onClick={() => select(option.ref)}
          >
            Select this exact version for a future stage
          </button>
        </div>
      ))}
      <h4>Actual generated/imported proposals from this stage</h4>
      {!(stage?.derived_input_refs ?? []).length && (
        <p>No actual ready derived input versions recorded.</p>
      )}
      {(stage?.derived_input_refs ?? []).map((ref) => (
        <div key={`${ref.origin}:${ref.version_id}`}>
          <p>
            {ref.origin} · {ref.version_id}. Selection is not approval.
          </p>
          {ref.origin === "normalized_external_proposal" && (
            <InputContentActions
              reference={{
                kind: "normalized",
                id: ref.version_id,
                sha256: ref.content_sha256,
              }}
            />
          )}
          <button
            disabled={
              ref.origin === "unknown" ||
              refs.some((value) => value.version_id === ref.version_id)
            }
            onClick={() => select(ref)}
          >
            Select exact derived version
          </button>
        </div>
      ))}
      <h4>Explicit ordered draft — not applied</h4>
      <ol>
        {refs.map((ref, index) => (
          <li key={`${ref.origin}:${ref.version_id}`}>
            {ref.origin} · {ref.version_id}
            <button
              onClick={() =>
                setRefs((old) => old.filter((_, i) => i !== index))
              }
            >
              Remove from future selection
            </button>
            <button
              disabled={index === 0}
              onClick={() =>
                setRefs((old) => {
                  const next = [...old];
                  [next[index - 1], next[index]] = [
                    next[index],
                    next[index - 1],
                  ];
                  return next;
                })
              }
            >
              Move earlier
            </button>
          </li>
        ))}
      </ol>
      {changed && (
        <p role="status">
          Local draft changed, not applied. Server CAS decides whether it can
          become the next selection. A concurrently claimed stage keeps its old
          frozen input.
        </p>
      )}
      {error !== null && <ErrorState error={error} />}
      <WorkflowControls
        ownerId={ownerId}
        workflow={workflow}
        futureRefs={changed ? refs : undefined}
        refresh={refresh}
      />
    </section>
  );
}
