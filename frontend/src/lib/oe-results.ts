import * as sdk from "@/client/sdk.gen";
import type {
  NormalExecutionRecord,
  NormalExecutionView,
  NormalPairComparison,
} from "@/client/types.gen";
import { oeReply } from "./oe-provider-api";
export const oeRecords = (run: string, signal?: AbortSignal) =>
  oeReply(() =>
    sdk.listOeStructuredExecutions({
      path: { run_id: run },
      signal,
      throwOnError: false,
    }),
  );
export const oeResult = (
  run: string,
  execution: string,
  signal?: AbortSignal,
) =>
  oeReply(() =>
    sdk.readOeStructuredExecution({
      path: { run_id: run, execution_id: execution },
      signal,
      throwOnError: false,
    }),
  );
export async function oeRelease(run: string, execution: string) {
  const receipt = await oeReply(() =>
    sdk.releaseOeStructuredExecution({
      path: { run_id: run, execution_id: execution },
      throwOnError: false,
    }),
  );
  if (
    receipt.run_id !== run ||
    receipt.execution_id !== execution ||
    receipt.qualification !== "exposure_record_not_execution_authority" ||
    receipt.extent !== "full_retained_factor_and_market_datasets" ||
    receipt.exposure_ids.length !== 2 ||
    new Set(receipt.exposure_ids).size !== 2
  )
    throw new Error(
      "Exact two-source release receipt differs; no result was authorized locally.",
    );
  return receipt;
}
export const oeComparison = (
  run: string,
  before: string,
  after: string,
  signal?: AbortSignal,
) =>
  oeReply(() =>
    sdk.compareOeStructuredExecutions({
      path: { run_id: run },
      query: { before, after },
      signal,
      throwOnError: false,
    }),
  );
export function releaseRequired(error: unknown) {
  const value = error as { status?: unknown; code?: unknown } | null;
  return (
    value?.status === 409 && value.code === "engine_result_release_required"
  );
}
export function exactNormalResult(
  value: NormalExecutionView,
  run: string,
  record: NormalExecutionRecord,
) {
  const e = value.execution,
    m = value.measurement;
  if (
    value.qualification !== "fixed_snapshot_comparison_not_causal_gain" ||
    value.candidate_id !== record.candidate_id ||
    e.run_id !== run ||
    e.execution_id !== record.execution_id ||
    e.structured_strategy_version_id !== record.program_version_id ||
    e.execution_snapshot_id !== record.snapshot_id ||
    e.execution_snapshot_run_id !== record.source_run_id ||
    m.execution_id !== e.execution_id ||
    m.structured_strategy_version_id !== e.structured_strategy_version_id ||
    m.snapshot_id !== e.execution_snapshot_id ||
    m.result_sha256 !== e.result_sha256 ||
    m.market_sha256 !== e.market_sha256
  )
    throw new Error(
      "Released result differs from the selected candidate, execution or snapshot.",
    );
  return value;
}
export function exactNormalPair(
  value: NormalPairComparison,
  run: string,
  before: NormalExecutionRecord,
  after: NormalExecutionRecord,
) {
  if (
    before.candidate_id === after.candidate_id ||
    after.parent_candidate_id !== before.candidate_id ||
    before.snapshot_id !== after.snapshot_id ||
    value.qualification !== "fixed_snapshot_comparison_not_causal_gain"
  )
    throw new Error(
      "Exact direct parent and child on one fixed snapshot required.",
    );
  exactNormalResult(value.before, run, before);
  exactNormalResult(value.after, run, after);
  return value;
}
