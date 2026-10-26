import { deploymentUrl } from "./deployment";
import { queryOptions } from "@tanstack/react-query";
import { clearSession, readSession } from "./session";

export type FactorUsage = {
  declared: number;
  explicit_read: number;
  experiment: number;
  observation_coverage: string;
};
export type FactorPresentation = {
  id: string;
  name: string;
  category: string | null;
  bookmarked: boolean;
  presentation_revision: number;
};
export type FactorListItem = FactorPresentation & {
  latest_version_id: string;
  ordinal: number;
  definition_sha256: string;
  kind: string;
  calculation_state: string;
  selected_snapshot_id: string | null;
  coverage: {
    row_count: number;
    valid_count: number;
    missing_count: number;
  } | null;
  analysis_status: string;
  usage: FactorUsage;
};
export type FactorVersion = {
  id: string;
  factor_id: string;
  ordinal: number;
  parent_version_id: string | null;
  definition: Record<string, unknown>;
  definition_sha256: string;
  code: string;
  code_sha256: string;
  source_kind: string;
  source_provenance: Record<string, unknown>;
  created_at: string;
};
export type FactorSnapshot = {
  id: string;
  factor_version_id: string;
  dataset_version_id: string;
  definition_sha256: string;
  input_digest: string;
  output_digest: string;
  manifest_sha256: string;
  row_count: number;
  valid_count: number;
  missing_count: number;
  manifest: Record<string, unknown>;
  local_verification: Record<string, unknown>;
  source_claims: Record<string, unknown>;
  created_at: string;
};
export type FactorDetail = {
  version: FactorVersion;
  snapshots: FactorSnapshot[];
  evaluations: Record<string, unknown>[];
  usage: FactorUsage;
};
export type FactorPage<T> = { data: T[]; count: number };
export type FactorValueRow = {
  event_time: string;
  session_date: string;
  listing_id: string;
  factor_version_id: string;
  available_at: string;
  value: number | null;
  value_state: string;
  missing_reason: string | null;
  input_window_start: string;
  input_window_end: string;
  input_evidence_digest: string;
};
export type FactorValues = FactorPage<FactorValueRow> & {
  snapshot_id: string;
  manifest_sha256: string;
  output_sha256: string;
  scope: unknown;
  downsampled: false;
};
export type FactorCalculationSample = {
  factor_version_id: string;
  listing_id: string;
  session_date: string;
  operator: "momentum" | "risk_adjusted_momentum" | "cross_sectional_rank";
  inputs: {
    listing_id: string;
    session_date: string;
    close: number | null;
    available_at: string;
  }[];
  peer_scores: [string, number][];
  output: FactorValueRow;
  explanation_source: "fixed_fixture_recomputed";
};
export type FactorSampleResponse = {
  snapshot_id: string;
  listing_id: string;
  session_date: string;
  evidence_status: "available" | "unavailable";
  samples: FactorCalculationSample[];
};
export type FactorComparisonValues = {
  comparison_id: string;
  data: {
    session_date: string;
    listing_id: string;
    event_time: string;
    left: FactorValueRow | null;
    right: FactorValueRow | null;
    delta: number | null;
    delta_reason: string | null;
  }[];
  count: number;
  scope: string;
};
export type FactorCapabilities = {
  enabled: boolean;
  package_import_enabled: boolean;
};

export async function factorRequest<T>(
  path: string,
  options: {
    signal?: AbortSignal;
    method?: string;
    body?: unknown;
    key?: string;
    blob?: boolean;
  } = {},
): Promise<T> {
  const headers: Record<string, string> = {};
  const session = readSession();
  if (session) headers.Authorization = `Bearer ${session.accessToken}`;
  if (options.key) headers["Idempotency-Key"] = options.key;
  const binary = options.body instanceof Blob;
  if (options.body !== undefined)
    headers["Content-Type"] = binary ? "application/zip" : "application/json";
  const response = await fetch(deploymentUrl(`/api/v1${path}`), {
    method: options.method || "GET",
    headers,
    body:
      options.body === undefined
        ? undefined
        : binary
          ? (options.body as Blob)
          : JSON.stringify(options.body),
    signal: AbortSignal.any([
      ...(options.signal ? [options.signal] : []),
      AbortSignal.timeout(20_000),
    ]),
  });
  if (response.status === 401) clearSession(true);
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as {
      message?: string;
      detail?: string;
      code?: string;
      correlation_id?: string;
    };
    throw {
      ...payload,
      message:
        payload.message ||
        (typeof payload.detail === "string"
          ? payload.detail
          : `Factor request failed (${response.status})`),
      status: response.status,
    };
  }
  return (options.blob ? await response.blob() : await response.json()) as T;
}
const get = <T>(key: readonly unknown[], path: string) =>
  queryOptions({
    queryKey: ["factors", ...key],
    queryFn: ({ signal }) => factorRequest<T>(path, { signal }),
  });
export const factorCapabilitiesOptions = get<FactorCapabilities>(
  ["capabilities"],
  "/factors/capabilities",
);
export const factorListOptions = (
  search = "",
  offset = 0,
  bookmarked = false,
) =>
  get<FactorPage<FactorListItem>>(
    ["list", search, offset, bookmarked],
    `/factors?limit=25&offset=${offset}&search=${encodeURIComponent(search)}${bookmarked ? "&bookmarked=true" : ""}`,
  );
export const factorPresentationOptions = (id: string) =>
  get<FactorPresentation>(
    ["presentation", id],
    `/factors/${encodeURIComponent(id)}`,
  );
export const factorVersionsOptions = (id: string, offset = 0) =>
  get<FactorPage<FactorVersion>>(
    ["versions", id, offset],
    `/factors/${encodeURIComponent(id)}/versions?limit=100&offset=${offset}`,
  );
export const factorDetailOptions = (id: string, version: string) =>
  get<FactorDetail>(
    ["detail", id, version],
    `/factors/${encodeURIComponent(id)}/versions/${encodeURIComponent(version)}`,
  );
export const factorSnapshotOptions = (id: string) =>
  get<FactorSnapshot>(
    ["snapshot", id],
    `/factor-snapshots/${encodeURIComponent(id)}`,
  );
export const factorValuesOptions = (id: string, offset = 0, listing?: string) =>
  get<FactorValues>(
    ["values", id, offset, listing],
    `/factor-snapshots/${encodeURIComponent(id)}/values?limit=50&offset=${offset}${listing ? `&listing_id=${encodeURIComponent(listing)}` : ""}`,
  );
export const factorSampleOptions = (
  id: string,
  listing: string,
  date: string,
) =>
  get<FactorSampleResponse>(
    ["sample", id, listing, date],
    `/factor-snapshots/${encodeURIComponent(id)}/sample?listing_id=${encodeURIComponent(listing)}&session_date=${encodeURIComponent(date)}`,
  );

export const factorComparisonValuesOptions = (id: string, offset = 0) =>
  get<FactorComparisonValues>(
    ["comparison-values", id, offset],
    `/factor-comparisons/${encodeURIComponent(id)}/values?limit=50&offset=${offset}`,
  );

export type FactorImportAdmission = {
  id: string;
  archive_sha256: string;
  status: "queued" | "processing" | "succeeded" | "failed" | "blocked";
  generation: number;
  result_envelope_id: string | null;
  error_code: string | null;
  history_completeness?: "unknown";
  exposure_history?: unknown;
  local_bindings?: {
    factor_id: string;
    version_id: string;
    source_version_id: string;
    snapshots: {
      source_snapshot_id: string;
      snapshot_id?: string;
      status: string;
      reason?: string;
    }[];
    execution_binding_status: string;
    objects: unknown;
  };
};
export const factorImportOptions = (id: string) =>
  get<FactorImportAdmission>(
    ["import", id],
    `/factor-imports/${encodeURIComponent(id)}`,
  );
