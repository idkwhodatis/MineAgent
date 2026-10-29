import { useState } from "react";
import { queryOptions, useQuery } from "@tanstack/react-query";
import { ErrorState, Loading, Panel } from "@/components/common";
import { deploymentUrl } from "@/lib/deployment";
import { completeFactorRead, factorRequest } from "@/lib/factor-api";
import type {
  StructuredExecutionRecords,
  StructuredExecutionRecord,
} from "@/client/types.gen";

export const normalOEUsagePath = (
  factor: string,
  version: string,
  offset: number,
) =>
  `/factors/${encodeURIComponent(factor)}/versions/${encodeURIComponent(version)}/normal-oe-executions?offset=${offset}`;
export const canAdvanceNormalOEUsage = (offset: number, count: number) =>
  count === 50 && offset < 100_000;
export function normalOEUsageLink(
  row: StructuredExecutionRecord,
  factor: string,
  version: string,
) {
  if (
    row.factor_id !== factor ||
    row.factor_version_id !== version ||
    row.qualification !== "identity_metadata_not_result_release"
  )
    return null;
  return deploymentUrl(
    `/engine-runs/structured-search/runs/${encodeURIComponent(row.run_id)}`,
  );
}

export const normalOEUsageOptions = (
  factorId: string,
  versionId: string,
  offset: number,
) =>
  queryOptions({
    queryKey: ["factor-normal-oe-usage", factorId, versionId, offset],
    // Like other factor reads, finish the bounded body across observer remounts.
    // Unmount is not proof that the server-side read transaction has ended.
    queryFn: () =>
      completeFactorRead(() =>
        factorRequest<StructuredExecutionRecords>(
          normalOEUsagePath(factorId, versionId, offset),
        ),
      ),
    retry: false,
  });

export function NormalOEFactorUsage({
  factorId,
  versionId,
}: {
  factorId: string;
  versionId: string;
}) {
  const [offset, setOffset] = useState(0);
  const records = useQuery(normalOEUsageOptions(factorId, versionId, offset));
  return (
    <Panel title="Normal OpenEvolve strategy usage — metadata only">
      <p>
        These owned closed-program executions use this exact factor version.
        This is not native Python checkpoint history. Discovery does not release
        metrics, fills or comparisons; inspect the exact execution in its normal
        search history and explicitly release it there.
      </p>
      {records.isPending ? (
        <Loading />
      ) : records.error ? (
        <ErrorState
          error={records.error}
          retry={() => void records.refetch({ cancelRefetch: false })}
        />
      ) : (
        <>
          {!records.data.data.length && (
            <p>No retained normal OE executions on this page.</p>
          )}
          <ul>
            {records.data.data.map((row) => {
              const href = normalOEUsageLink(row, factorId, versionId);
              return (
                <li key={row.execution_id}>
                  {href ? (
                    <>
                      <a href={href}>Inspect normal OE run {row.run_id}</a>
                      <p>
                        Exact execution {row.execution_id}; snapshot{" "}
                        {row.execution_snapshot_id}; program version{" "}
                        {row.structured_strategy_version_id}.
                      </p>
                    </>
                  ) : (
                    <p role="alert">
                      Execution identity differs from the selected factor
                      version.
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
          <button
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - 50))}
          >
            Previous normal OE usage
          </button>
          <button
            disabled={
              !canAdvanceNormalOEUsage(offset, records.data.data.length)
            }
            onClick={() => setOffset(offset + 50)}
          >
            Next normal OE usage
          </button>
        </>
      )}
    </Panel>
  );
}
