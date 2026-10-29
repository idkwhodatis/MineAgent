import { useState } from "react";
import { Link, useParams } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { ErrorState, Loading, PageHeader, Panel } from "@/components/common";
import {
  exportExecutionSnapshot,
  releasedAnalysisOptions,
  releasedSnapshotValuesOptions,
  releaseEngineResult,
  type EngineResultKind,
} from "@/lib/engine-runs-api";

import {
  RDAnalysisChart,
  observationForAnalysis,
  type RDAnalysisObservation,
} from "./rd-analysis-chart";

import { ObservationSteer } from "./observation-steer";

function requiresRelease(error: unknown) {
  return (
    !!error &&
    typeof error === "object" &&
    "code" in error &&
    error.code === "engine_result_release_required"
  );
}
function ReleaseNotice({
  runId,
  id,
  kind,
  reload,
}: {
  runId: string;
  id: string;
  kind: EngineResultKind;
  reload: () => Promise<unknown>;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function release() {
    setPending(true);
    setError(null);
    try {
      await releaseEngineResult(runId, id, kind);
      await reload();
    } catch (e) {
      setError(e);
    } finally {
      setPending(false);
    }
  }
  return (
    <Panel title="Explicit historical result release required">
      <p>
        Viewing these results permanently records exposure to the full retained
        factor dataset
        {kind === "direct_analysis" ? " and future-label dataset" : ""}. It can
        classify overlapping holdout windows as previously exposed. This does
        not certify unseen performance or authorize new execution.
      </p>
      <p>
        A retry after a lost response reuses the same immutable release. No
        values are shown before the exposure transaction commits.
      </p>
      <button type="button" disabled={pending} onClick={() => void release()}>
        {pending ? "Recording exposure…" : "Release historical result"}
      </button>
      {error != null && <ErrorState error={error} />}
    </Panel>
  );
}
const historicalNotice =
  "Retained bytes verified for this historical read. Not re-executed, not a live execution grant, not verified point-in-time or out-of-sample performance.";

export function ReleasedSnapshotPanel({
  runId,
  snapshotId,
}: {
  runId: string;
  snapshotId: string;
}) {
  const [offset, setOffset] = useState(0);
  const [exportError, setExportError] = useState<unknown>(null);
  const [exporting, setExporting] = useState(false);
  const result = useQuery({
    ...releasedSnapshotValuesOptions(runId, snapshotId, offset),
    retry: false,
  });
  async function download() {
    setExporting(true);
    setExportError(null);
    try {
      const blob = await exportExecutionSnapshot(runId, snapshotId);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `execution-snapshot-${snapshotId}.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setExportError(e);
    } finally {
      setExporting(false);
    }
  }
  if (result.isPending) return <Loading />;
  if (result.error)
    return requiresRelease(result.error) ? (
      <ReleaseNotice
        runId={runId}
        id={snapshotId}
        kind="execution_snapshot"
        reload={result.refetch}
      />
    ) : (
      <ErrorState error={result.error} retry={() => void result.refetch()} />
    );
  return (
    <Panel title="Released historical factor values">
      <p>{historicalNotice}</p>
      <p>
        {result.data.count} retained rows. Exposure receipts:{" "}
        {result.data.exposure_ids.join(", ")}.
      </p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Session</th>
              <th>Listing</th>
              <th>Value</th>
              <th>State</th>
              <th>Available at</th>
            </tr>
          </thead>
          <tbody>
            {result.data.data.map((row) => (
              <tr key={`${row.listing_id}-${row.session_date}`}>
                <td>{row.session_date}</td>
                <td>{row.listing_id}</td>
                <td>{row.value ?? "Not defined"}</td>
                <td>{row.value_state}</td>
                <td>{row.available_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <button
        type="button"
        disabled={offset === 0}
        onClick={() => setOffset(Math.max(0, offset - 100))}
      >
        Previous values
      </button>
      <button
        type="button"
        disabled={offset + 100 >= result.data.count}
        onClick={() => setOffset(offset + 100)}
      >
        Next values
      </button>
      <button
        type="button"
        disabled={exporting}
        onClick={() => void download()}
      >
        {exporting ? "Verifying export…" : "Export historical snapshot JSON"}
      </button>
      {exportError != null && <ErrorState error={exportError} />}
    </Panel>
  );
}

export function DirectAnalysisPage() {
  const { runId, analysisId } = useParams({
    from: "/workspace/engine-runs/$runId/analyses/$analysisId",
  });
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
        title="Historical direct factor analysis"
        subtitle="An immutable declared objective and retained measurement; never a live execution grant."
      />
      <DirectAnalysisPanel runId={runId} analysisId={analysisId} />
    </div>
  );
}
export function DirectAnalysisPanel({
  runId,
  analysisId,
  expectedSnapshotId,
  onObservation,
}: {
  runId: string;
  analysisId: string;
  expectedSnapshotId?: string;
  onObservation?: (observation: RDAnalysisObservation) => void;
}) {
  const [observation, setObservation] = useState<RDAnalysisObservation>();
  const result = useQuery({
    ...releasedAnalysisOptions(runId, analysisId),
    retry: false,
  });
  const currentObservation = result.data
    ? observationForAnalysis(observation, result.data)
    : undefined;
  if (
    result.data &&
    (result.data.run_id !== runId ||
      result.data.analysis_id !== analysisId ||
      (expectedSnapshotId &&
        result.data.execution_snapshot_id !== expectedSnapshotId))
  )
    return (
      <ErrorState
        error={
          new Error("Analysis does not match the selected execution snapshot.")
        }
      />
    );
  return (
    <>
      {result.isPending ? (
        <Loading />
      ) : result.error ? (
        requiresRelease(result.error) ? (
          <ReleaseNotice
            runId={runId}
            id={analysisId}
            kind="direct_analysis"
            reload={result.refetch}
          />
        ) : (
          <ErrorState
            error={result.error}
            retry={() => void result.refetch()}
          />
        )
      ) : (
        <>
          <Panel title="Released direct analysis">
            <p>{historicalNotice}</p>
            <dl>
              <dt>Objective measurement</dt>
              <dd>{result.data.measurement.status}</dd>
              <dt>Reason</dt>
              <dd>{result.data.measurement.reason}</dd>
              <dt>Measured value</dt>
              <dd>{result.data.measurement.value ?? "Not evaluated"}</dd>
              <dt>Mean IC</dt>
              <dd>{result.data.result.ic ?? "Not defined"}</dd>
              <dt>Mean rank IC</dt>
              <dd>{result.data.result.rank_ic ?? "Not defined"}</dd>
            </dl>
            <p>
              Defined days: {result.data.measurement.defined_days}; defined
              pairs: {result.data.measurement.defined_pairs}; total pairs:{" "}
              {result.data.measurement.total_pairs}.
            </p>
            <p className="break-all">
              Result SHA-256: {result.data.result_sha256}
              <br />
              Measurement SHA-256: {result.data.measurement_sha256}
            </p>
            <Link
              to="/engine-runs/$runId/snapshots/$snapshotId"
              params={{ runId, snapshotId: result.data.execution_snapshot_id }}
              className="text-link"
            >
              Open execution snapshot
            </Link>
          </Panel>
          <Panel title="Retained daily analysis">
            <RDAnalysisChart
              key={`${result.data.analysis_id}:${result.data.result_sha256}`}
              analysis={result.data}
              onObservation={onObservation ?? setObservation}
            />
            {currentObservation && (
              <ObservationSteer
                prepared
                key={JSON.stringify(currentObservation)}
                observation={currentObservation}
                onClose={() => setObservation(undefined)}
              />
            )}
          </Panel>
        </>
      )}
    </>
  );
}
