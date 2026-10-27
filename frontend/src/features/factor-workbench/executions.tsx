import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { ErrorState, Loading, Panel } from "@/components/common";
import { Button } from "@/components/ui/button";
import { deploymentUrl } from "@/lib/deployment";
import { factorExecutionOptions } from "@/lib/factor-execution-api";

export function FactorExecutions({
  factorId,
  versionId,
}: {
  factorId: string;
  versionId: string;
}) {
  const [offset, setOffset] = useState(0);
  const records = useQuery(factorExecutionOptions(factorId, versionId, offset));
  return (
    <Panel title="Research execution snapshots">
      <p>
        Dedicated immutable execution records for this exact factor version.
        These are not legacy provider snapshots and are never selected into
        legacy comparisons or exports.
      </p>
      <p className="muted">
        A recorded completion is not proof of analysis success. Values and
        analysis measurements require an explicit data-exposure release on the
        result page; opening this list releases neither.
      </p>
      {records.isPending ? (
        <Loading />
      ) : records.error ? (
        <ErrorState
          error={records.error}
          retry={() => void records.refetch()}
        />
      ) : (
        <>
          {!records.data.data.length && (
            <p>
              No execution records on this page. No calculation or analysis
              result is implied.
            </p>
          )}
          <ul className="space-y-4">
            {records.data.data.map((record) =>
              record.factor_id !== factorId ||
              record.factor_version_id !== versionId ||
              record.kind !== "execution_snapshot" ? (
                <li key={record.execution_snapshot_id} role="alert">
                  Execution identity does not match the selected version.
                </li>
              ) : (
                <li key={record.execution_snapshot_id} className="break-all">
                  <Link
                    to="/engine-runs/$runId/snapshots/$snapshotId"
                    params={{
                      runId: record.run_id,
                      snapshotId: record.execution_snapshot_id,
                    }}
                    className="text-link"
                  >
                    Open execution snapshot {record.execution_snapshot_id}
                  </Link>
                  <p>
                    {record.publication_state === "completed"
                      ? "Publication completion recorded"
                      : "Publication pending — no result available"}
                  </p>
                  <p>
                    {record.analysis_state === "result_recorded"
                      ? "Direct analysis result recorded; measurement is not released here."
                      : record.analysis_state === "pending"
                        ? "Direct analysis declared; no result recorded."
                        : "No direct analysis declared for this snapshot."}
                  </p>
                  {record.analysis_id && (
                    <p>Dedicated analysis ID: {record.analysis_id}</p>
                  )}
                  {record.analysis_id &&
                    record.analysis_state === "result_recorded" && (
                      <a
                        className="text-link"
                        href={deploymentUrl(
                          `/engine-runs/${record.run_id}/analyses/${record.analysis_id}`,
                        )}
                      >
                        Open declared direct analysis
                      </a>
                    )}
                </li>
              ),
            )}
          </ul>
          <div className="flex gap-2">
            <Button
              variant="outline"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - 20))}
            >
              Previous executions
            </Button>
            <Button
              variant="outline"
              disabled={offset + 20 >= records.data.count}
              onClick={() => setOffset(offset + 20)}
            >
              Next executions
            </Button>
          </div>
        </>
      )}
    </Panel>
  );
}
