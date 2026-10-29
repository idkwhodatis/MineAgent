import type { WorkflowStageList } from "@/workflow-contracts/types.gen";

export function WorkflowStageNavigation({
  page,
  pending,
  currentStageId,
  selectedStageId,
  canPrevious,
  onCurrent,
  onSelect,
  onPrevious,
  onNext,
}: {
  page?: WorkflowStageList;
  pending: boolean;
  currentStageId?: string;
  selectedStageId?: string;
  canPrevious: boolean;
  onCurrent: () => void;
  onSelect: (id: string) => void;
  onPrevious: () => void;
  onNext: () => void;
}) {
  return (
    <nav aria-label="Recorded workflow stages">
      <h3>Recorded stages</h3>
      <p>
        Navigation metadata only. Select a stage to load its actual inputs,
        results and guidance consumption.
      </p>
      <button disabled={!currentStageId} onClick={onCurrent}>
        Follow current stage
      </button>
      <ol
        aria-label="Recorded stage timeline"
        className="ml-3 border-l-2 border-slate-400 pl-6"
      >
        {page?.items.map((item) => (
          <li
            key={item.stage_id}
            className="relative mb-5 rounded border border-slate-300 p-3"
          >
            <span
              aria-hidden="true"
              className="absolute -left-8 top-4 h-3 w-3 rounded-full border-2 border-slate-500 bg-white"
            />
            <button
              aria-current={
                selectedStageId === item.stage_id ? "step" : undefined
              }
              onClick={() => onSelect(item.stage_id)}
            >
              Stage {item.ordinal}: {item.kind}
              {currentStageId === item.stage_id ? " (current)" : ""}
            </button>
            <p>
              <time dateTime={item.created_at}>{item.created_at}</time> · input
              version {item.input_revision_id}
            </p>
            <p className="break-all">
              Definition commitment: {item.definition_sha256}
            </p>
            <small>
              Receipt and outcome status are loaded separately; ordering does
              not prove success or a dependency.
            </small>
          </li>
        ))}
      </ol>
      {page && page.items.length === 0 && (
        <p>No stages recorded on this page.</p>
      )}
      <button disabled={pending || !canPrevious} onClick={onPrevious}>
        Previous stages
      </button>
      <button
        disabled={pending || !page?.has_more || !page.items.length}
        onClick={onNext}
      >
        More stages
      </button>
    </nav>
  );
}

// Reading a historical stage must never retarget current-stage steering controls.
export function workflowStageTargets(
  currentStageId?: string,
  selectedStageId?: string,
) {
  return { currentStageId, stageId: selectedStageId ?? currentStageId };
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export function stageSelectionFromHash(hash: string, workflowId: string) {
  const params = new URLSearchParams(hash.replace(/^#/, ""));
  const stage = params.get("research_stage");
  return params.get("research_workflow") === workflowId &&
    stage &&
    UUID.test(stage)
    ? stage
    : undefined;
}
export function stageSelectionHash(
  hash: string,
  workflowId: string,
  stageId?: string,
) {
  const params = new URLSearchParams(hash.replace(/^#/, ""));
  params.delete("research_workflow");
  params.delete("research_stage");
  if (stageId) {
    if (!UUID.test(workflowId) || !UUID.test(stageId))
      throw new Error("Invalid workflow stage identity.");
    params.set("research_workflow", workflowId);
    params.set("research_stage", stageId);
  }
  return params.toString() ? `#${params}` : "";
}
