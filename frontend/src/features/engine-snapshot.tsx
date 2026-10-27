import { Link, useParams } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { ErrorState, Loading, PageHeader, Panel } from "@/components/common";
import { executionSnapshotOptions } from "@/lib/engine-runs-api";
import { ReleasedSnapshotPanel } from "./engine-results";
import { formatDate } from "@/lib/format";

export function ExecutionSnapshotPage() {
  const { runId, snapshotId } = useParams({
    from: "/workspace/engine-runs/$runId/snapshots/$snapshotId",
  });
  const snapshot = useQuery(executionSnapshotOptions(runId, snapshotId));
  return (
    <div className="page">
      <Link
        to="/engine-runs/$runId"
        params={{ runId }}
        search={{ offset: 0 }}
        className="text-link"
      >
        Back to research history
      </Link>
      <PageHeader
        title="Execution snapshot metadata"
        subtitle="Recorded commitments, separate from legacy factor snapshots."
      />
      {snapshot.isPending ? (
        <Loading />
      ) : snapshot.error ? (
        <ErrorState
          error={snapshot.error}
          retry={() => void snapshot.refetch()}
        />
      ) : (
        <>
          <Panel title="Recorded publication">
            <p>
              {snapshot.data.state === "completed"
                ? "Completion was recorded"
                : "Publication pending — no completion recorded"}
            </p>
            <p className="muted">
              This read does not re-execute the factor or verify that retained
              artifact bytes are currently available. Publication is not
              evidence of analysis success or verified point-in-time
              availability.
            </p>
            <dl className="break-all">
              <dt>Protocol</dt>
              <dd>{snapshot.data.schema_version}</dd>
              <dt>Execution snapshot</dt>
              <dd>{snapshot.data.id}</dd>
              <dt>Source</dt>
              <dd>{snapshot.data.source_id}</dd>
              <dt>Factor version</dt>
              <dd>{snapshot.data.factor_version_id}</dd>
              <dt>Evaluation</dt>
              <dd>{snapshot.data.evaluation_id}</dd>
              <dt>Dataset</dt>
              <dd>{snapshot.data.dataset_version_id}</dd>
              <dt>Created · UTC</dt>
              <dd>{formatDate(snapshot.data.created_at)}</dd>
              <dt>Completed · UTC</dt>
              <dd>
                {snapshot.data.completed_at
                  ? formatDate(snapshot.data.completed_at)
                  : "Not recorded"}
              </dd>
            </dl>
          </Panel>
          <Panel title="Declared window">
            <p>
              {snapshot.data.window_start} ≤ session &lt;{" "}
              {snapshot.data.window_end}
            </p>
            <p>
              Data-derived counts and values require explicit historical result
              release.
            </p>
            <p>
              Dependency claim: input provided, not proven read or consumed by a
              decision.
            </p>
          </Panel>
          <Panel title="Immutable commitments">
            <dl className="break-all">
              <dt>Manifest SHA-256</dt>
              <dd>{snapshot.data.manifest_sha256}</dd>
              <dt>Input SHA-256</dt>
              <dd>{snapshot.data.input_sha256}</dd>
              <dt>Completed output SHA-256</dt>
              <dd>
                {snapshot.data.output_sha256 ?? "No completed output recorded"}
              </dd>
            </dl>
            <p className="muted">
              Values and analysis results are not released by this metadata
              endpoint. No simulated values, metrics, or success scores are
              shown.
            </p>
          </Panel>
          {snapshot.data.state === "completed" && (
            <ReleasedSnapshotPanel runId={runId} snapshotId={snapshotId} />
          )}
        </>
      )}
    </div>
  );
}
