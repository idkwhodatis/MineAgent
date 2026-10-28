import { queryOptions } from "@tanstack/react-query";
import { completeFactorRead, factorRequest } from "./factor-api";
import type {
  StructuredExecutionRecords,
  StructuredExecutionView,
  StructuredPairComparison,
  StructuredReadCapability,
  StructuredRelease,
} from "@/client/types.gen";
const get = <T>(key: readonly unknown[], path: string) =>
  queryOptions({
    queryKey: ["structured-results", ...key],
    queryFn: () => completeFactorRead(() => factorRequest<T>(path)),
    retry: false,
  });
const runPath = (run: string) => `/engine-runs/${encodeURIComponent(run)}`;
export const structuredCapabilityOptions = get<StructuredReadCapability>(
  ["capabilities"],
  "/structured-executions/capabilities",
);
export const factorStructuredOptions = (
  factor: string,
  version: string,
  offset = 0,
) =>
  get<StructuredExecutionRecords>(
    ["factor", factor, version, offset],
    `/factors/${encodeURIComponent(factor)}/versions/${encodeURIComponent(version)}/structured-executions?offset=${offset}`,
  );
export const structuredRecordsOptions = (run: string) =>
  get<StructuredExecutionRecords>(
    ["run", run],
    `${runPath(run)}/structured-execution-records`,
  );
export const structuredExecutionOptions = (run: string, id: string) =>
  get<StructuredExecutionView>(
    ["execution", run, id],
    `${runPath(run)}/structured-executions/${encodeURIComponent(id)}`,
  );
export const structuredComparisonOptions = (
  run: string,
  before: string,
  after: string,
) =>
  get<StructuredPairComparison>(
    ["pair", run, before, after],
    `${runPath(run)}/structured-comparison?before=${encodeURIComponent(before)}&after=${encodeURIComponent(after)}`,
  );
export const releaseStructuredExecution = (run: string, id: string) =>
  factorRequest<StructuredRelease>(
    `${runPath(run)}/structured-executions/${encodeURIComponent(id)}/release`,
    { method: "POST" },
  );
export function structuredReleaseRequired(error: unknown) {
  return (
    !!error &&
    typeof error === "object" &&
    "code" in error &&
    error.code === "engine_result_release_required"
  );
}
export function structuredPairSearch(value: Record<string, unknown>) {
  const uuid =
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  return {
    before:
      typeof value.before === "string" && uuid.test(value.before)
        ? value.before
        : undefined,
    after:
      typeof value.after === "string" && uuid.test(value.after)
        ? value.after
        : undefined,
  };
}
