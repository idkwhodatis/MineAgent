import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { ErrorState, Loading, PageHeader } from "@/components/common";
import { currentUserOptions } from "@/lib/api";
import { factorRequest } from "@/lib/factor-api";
import type { WorkflowAvailability } from "@/workflow-contracts/types.gen";
import { WorkflowEntry } from "./workflow-entry";
import { WorkflowHistory } from "./workflow-history";

export async function readWorkflowAvailability(
  signal?: AbortSignal,
): Promise<WorkflowAvailability> {
  try {
    return await factorRequest<WorkflowAvailability>(
      "/research-workflows/capabilities",
      { signal },
    );
  } catch (failure) {
    if ((failure as { status?: number } | null)?.status === 404)
      return {
        can_read: false,
        preflight_available: false,
        structured_preflight_available: false,
        reason_code: "workflow_routes_not_installed",
      };
    throw failure;
  }
}
const availabilityOptions = {
  queryKey: ["research-workflows", "capabilities"],
  queryFn: ({ signal }: { signal: AbortSignal }) =>
    readWorkflowAvailability(signal),
  retry: false,
};
export function ResearchWorkflowPage() {
  const owner = useQuery(currentUserOptions);
  const availability = useQuery(availabilityOptions);
  const navigate = useNavigate();
  if (owner.isPending || availability.isPending) return <Loading />;
  if (owner.error || availability.error)
    return <ErrorState error={owner.error ?? availability.error} />;
  return (
    <div className="page">
      <PageHeader
        title="Finite research workflows"
        subtitle="Manual guidance, normalized external input, and actual experiment feedback retain separate provenance."
      />
      <Link to="/engine-runs" search={{ offset: 0 }}>
        Back to engine history
      </Link>
      <WorkflowEntry
        key={owner.data.id}
        ownerId={owner.data.id}
        available={availability.data.preflight_available}
        structuredAvailable={
          availability.data.structured_preflight_available === true
        }
        reason={availability.data.reason_code}
        onCreated={(receipt) =>
          void navigate({
            to: "/research-workflows/$workflowId",
            params: { workflowId: receipt.workflow_id },
          })
        }
      />
    </div>
  );
}
export function ResearchWorkflowDetailPage() {
  const { workflowId } = useParams({
    from: "/workspace/research-workflows/$workflowId",
  });
  const owner = useQuery(currentUserOptions);
  const availability = useQuery(availabilityOptions);
  if (owner.isPending || availability.isPending) return <Loading />;
  if (owner.error || availability.error)
    return <ErrorState error={owner.error ?? availability.error} />;
  return (
    <div className="page">
      <Link to="/research-workflows">Back to workflow entry</Link>
      <WorkflowHistory
        key={`${owner.data.id}:${workflowId}`}
        id={workflowId}
        ownerId={owner.data.id}
        enabled={availability.data.can_read}
      />
    </div>
  );
}
