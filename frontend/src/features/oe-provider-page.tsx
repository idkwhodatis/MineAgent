import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { ErrorState, Loading, PageHeader, Panel } from "@/components/common";
import { currentUserOptions } from "@/lib/api";
import { oeCapabilities } from "@/lib/oe-provider-api";
import type { StructuredSearchCapabilities } from "@/client/types.gen";
import { OEProviderRunStatus } from "./oe-provider-run";
import { OEProviderEntry } from "./oe-provider-entry";

export function normalSearchAvailable(value?: StructuredSearchCapabilities) {
  return (
    value?.qualification === "metadata_not_submission_authority" &&
    value.routes_available === true &&
    value.can_preflight === true &&
    value.reason_code == null
  );
}
export function OEProviderPage() {
  const owner = useQuery(currentUserOptions);
  const capability = useQuery({
    queryKey: ["oe-provider-capabilities", owner.data?.id],
    queryFn: ({ signal }) => oeCapabilities(signal),
    enabled: !!owner.data,
    retry: false,
  });
  if (owner.isPending) return <Loading />;
  if (owner.error)
    return (
      <ErrorState error={owner.error} retry={() => void owner.refetch()} />
    );
  return (
    <div className="page">
      <PageHeader
        title="Fixed-factor strategy search"
        subtitle="OpenEvolve varies a bounded closed program against one explicitly released factor snapshot. It does not discover or fetch market data."
      />
      <Panel title="Prepare the factor first">
        <p>
          For a small synthetic demonstration, create the explicitly labeled
          demo dataset, then run factor research and release its execution
          snapshot. The demo is not real-market or point-in-time evidence.
        </p>
        <Link to="/engine-runs/preflight" className="text-link">
          Create a demo dataset or start factor research
        </Link>
      </Panel>
      {capability.isPending ? (
        <Loading />
      ) : capability.error ? (
        <ErrorState
          error={capability.error}
          retry={() => void capability.refetch()}
        />
      ) : !normalSearchAvailable(capability.data) ? (
        <Panel title="Search unavailable">
          <p>
            The installed server has not enabled this normal search profile. No
            model call or submission was made.
          </p>
          <p>{capability.data?.reason_code ?? "runtime_schema_unavailable"}</p>
        </Panel>
      ) : (
        <OEProviderEntry ownerId={owner.data.id} routesAvailable />
      )}
    </div>
  );
}

export function OEProviderRunPage() {
  const { runId } = useParams({
    from: "/workspace/engine-runs/structured-search/runs/$runId",
  });
  const owner = useQuery(currentUserOptions);
  if (owner.isPending) return <Loading />;
  if (owner.error)
    return (
      <ErrorState error={owner.error} retry={() => void owner.refetch()} />
    );
  return (
    <div className="page">
      <PageHeader
        title="Strategy search status"
        subtitle="Actual owned run and budget accounting; no unreleased result disclosure."
      />
      <OEProviderRunStatus
        key={`${owner.data.id}:${runId}`}
        ownerId={owner.data.id}
        runId={runId}
      />
    </div>
  );
}
