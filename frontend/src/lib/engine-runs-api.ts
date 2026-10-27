import { queryOptions } from "@tanstack/react-query";
import { factorRequest } from "./factor-api";

import type {
  EngineCapability as ServerEngineCapability,
  EngineDatasetOption,
  RdPreflight,
  RdSubmissionRequest,
  RdSeededSubmissionRequest,
  RdSeededPreflight,
  RdProviderSubmissionRequest,
  RdProviderSeedRequest,
  RdProviderPreflight,
} from "@/client/types.gen";
export type EngineCapability = ServerEngineCapability;
export type EngineRun = {
  id: string;
  purpose: "factor_research";
  engine: "rdagent";
  config_schema:
    "mineagent.factor-research.v1" | "mineagent.factor-research.v2";
  status: string;
  generation: number;
  dataset_version_id: string;
  strategy_version_id: null;
  task_name: string;
  created_at: string;
  updated_at: string;
  actions: {
    cancel: boolean;
    pause: false;
    resume: false;
    reason_code: string;
  };
};
export type EngineDetail = EngineRun & {
  config_sha256: string;
  task_description: string;
  max_iterations: number;
  budget_limits: Record<string, number>;
  error: string | null;
  evidence_scope: string;
};
export type EngineHistory = {
  id: string;
  attempt_id: string;
  generation: number;
  logical_step: number;
  created_at: string;
  source_id: string | null;
  source_sha256: string | null;
  factor_version_id: string | null;
  factor_id: string | null;
  generation_outcome: string | null;
  error_code: string | null;
  evaluated_source_id: string | null;
  adopted_source_id: string | null;
  continuation_source_id: string | null;
  evaluation_id: string | null;
  evaluation_status: string | null;
  evaluation_protocol: "legacy_result" | "execution_result" | null;
  execution_snapshot_publication_id: string | null;
  execution_snapshot_id: string | null;
  execution_snapshot_schema: "mineagent.factor-execution-snapshot.v1" | null;
  execution_snapshot_state: "publication_pending" | "completed" | null;
  execution_snapshot_input_sha256: string | null;
  execution_snapshot_output_sha256: string | null;
  execution_snapshot_manifest_sha256: string | null;
  snapshot_id: string | null;
  snapshot_input_sha256: string | null;
  snapshot_output_sha256: string | null;
};
export type EnginePage<T> = { data: T[]; count: number };
const get = <T>(key: readonly unknown[], path: string) =>
  queryOptions({
    queryKey: ["engine-history", ...key],
    queryFn: ({ signal }) => factorRequest<T>(path, { signal }),
  });
export const engineCapabilitiesOptions = get<{ engines: EngineCapability[] }>(
  ["capabilities"],
  "/engine-runs/capabilities",
);
export const engineRunsOptions = (offset = 0) =>
  get<EnginePage<EngineRun>>(
    ["list", offset],
    `/engine-runs?offset=${offset}&limit=25`,
  );
export const engineDetailOptions = (id: string) =>
  get<EngineDetail>(["detail", id], `/engine-runs/${encodeURIComponent(id)}`);
export const engineHistoryOptions = (id: string, offset = 0) =>
  get<EnginePage<EngineHistory>>(
    ["history", id, offset],
    `/engine-runs/${encodeURIComponent(id)}/history?offset=${offset}&limit=25`,
  );

export type ExecutionSnapshotMetadata = {
  id: string;
  schema_version: "mineagent.factor-execution-snapshot.v1";
  run_id: string;
  attempt_id: string;
  generation: number;
  generation_start_id: string;
  source_id: string;
  evaluation_id: string;
  factor_version_id: string;
  dataset_version_id: string;
  state: "publication_pending" | "completed";
  created_at: string;
  completed_at: string | null;
  window_start: string;
  window_end: string;
  expected_cutoffs: null;
  row_count: null;
  valid_count: null;
  manifest_sha256: string;
  input_sha256: string;
  output_sha256: string | null;
  dependency_claim: "provided_not_proven_read";
  qualification: "persisted_metadata_not_reexecuted_or_reverified";
  values_available: false;
  analysis_results_available: false;
};
export const executionSnapshotOptions = (runId: string, snapshotId: string) =>
  get<ExecutionSnapshotMetadata>(
    ["execution-snapshot", runId, snapshotId],
    `/engine-runs/${encodeURIComponent(runId)}/execution-snapshots/${encodeURIComponent(snapshotId)}`,
  );

export const engineDatasetsOptions = (offset = 0) =>
  get<EnginePage<EngineDatasetOption>>(
    ["datasets", offset],
    `/engine-runs/datasets?offset=${offset}&limit=25`,
  );
export type EngineRequest =
  | RdSubmissionRequest
  | RdSeededSubmissionRequest
  | RdProviderSubmissionRequest
  | RdProviderSeedRequest;
export type EnginePreflight =
  RdPreflight | RdSeededPreflight | RdProviderPreflight;
export const preflightEngineRun = (body: EngineRequest) =>
  factorRequest<EnginePreflight>("/engine-runs/preflight", {
    method: "POST",
    body,
  });

export type EngineResultKind = "execution_snapshot" | "direct_analysis";
export const engineResultPath = (
  runId: string,
  resourceId: string,
  kind: EngineResultKind,
) =>
  `/engine-runs/${encodeURIComponent(runId)}/${kind === "execution_snapshot" ? "execution-snapshots" : "analyses"}/${encodeURIComponent(resourceId)}`;
export const releasedSnapshotValuesOptions = (
  runId: string,
  id: string,
  offset = 0,
) =>
  get<import("@/client/types.gen").ExecutionSnapshotValues>(
    ["values", runId, id, offset],
    `${engineResultPath(runId, id, "execution_snapshot")}/values?offset=${offset}&limit=100`,
  );
export const releasedAnalysisOptions = (runId: string, id: string) =>
  get<import("@/client/types.gen").DirectAnalysisResult>(
    ["analysis", runId, id],
    engineResultPath(runId, id, "direct_analysis"),
  );
export const releaseEngineResult = (
  runId: string,
  id: string,
  kind: EngineResultKind,
) =>
  factorRequest<import("@/client/types.gen").EngineResultRelease>(
    `${engineResultPath(runId, id, kind)}/release`,
    { method: "POST" },
  );
export const exportExecutionSnapshot = (runId: string, id: string) =>
  factorRequest<Blob>(
    `${engineResultPath(runId, id, "execution_snapshot")}/export`,
    { blob: true },
  );
