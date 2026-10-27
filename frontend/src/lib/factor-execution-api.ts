import { queryOptions } from "@tanstack/react-query";
import { completeFactorRead, factorRequest } from "./factor-api";

export type FactorExecutionRecord = {
  kind: "execution_snapshot";
  factor_id: string;
  factor_version_id: string;
  run_id: string;
  execution_snapshot_id: string;
  source_id: string;
  evaluation_id: string;
  publication_state: "publication_pending" | "completed";
  analysis_id: string | null;
  analysis_state: "not_declared" | "pending" | "result_recorded";
  created_at: string;
  qualification: "identity_metadata_not_result_release";
};
export const factorExecutionOptions = (
  factorId: string,
  versionId: string,
  offset = 0,
) =>
  queryOptions({
    queryKey: [
      "factors",
      factorId,
      "versions",
      versionId,
      "execution-records",
      offset,
    ],
    queryFn: () =>
      completeFactorRead(() =>
        factorRequest<{ data: FactorExecutionRecord[]; count: number }>(
          `/factors/${factorId}/versions/${versionId}/execution-records?offset=${offset}&limit=20`,
        ),
      ),
  });

/** Bounded metadata-only lookup; never substitute a latest record on deep reload. */
export const exactFactorExecutionOptions = (
  factorId: string,
  versionId: string,
  snapshotId: string,
) =>
  queryOptions({
    queryKey: ["factor-execution-exact", factorId, versionId, snapshotId],
    enabled: Boolean(snapshotId),
    queryFn: () =>
      completeFactorRead(async (requireSameSession) => {
        for (let offset = 0; offset < 1000; offset += 100) {
          requireSameSession();
          const page = await factorRequest<{
            data: FactorExecutionRecord[];
            count: number;
          }>(
            `/factors/${factorId}/versions/${versionId}/execution-records?offset=${offset}&limit=100`,
          );
          requireSameSession();
          if (
            page.data.some(
              (row) =>
                row.kind !== "execution_snapshot" ||
                row.factor_id !== factorId ||
                row.factor_version_id !== versionId,
            )
          )
            throw new Error(
              "Execution metadata does not match the exact factor version.",
            );
          const found = page.data.find(
            (row) => row.execution_snapshot_id === snapshotId,
          );
          if (found) return found;
          if (offset + 100 >= page.count) break;
        }
        throw new Error(
          "Exact execution unavailable within the bounded 1,000-record metadata lookup. No replacement was selected.",
        );
      }),
  });
