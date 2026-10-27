import { seededPreflightHref } from "@/lib/rd-provider-inputs";
import { useRef, useState } from "react";
import { Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { ErrorState, Loading, Panel } from "@/components/common";
import {
  Field,
  JsonDetails,
  Metadata,
  Pagination,
} from "@/components/data-common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  factorComparisonValuesOptions,
  factorDetailOptions,
  factorRequest,
  factorSampleOptions,
  factorSnapshotOptions,
  factorValuesOptions,
  type FactorCalculationSample,
  type FactorDetail,
  type FactorSnapshot,
  type FactorUsage,
  type FactorVersion,
} from "@/lib/factor-api";
import { factorValue, submissionKey, type FactorSelection } from "./selection";

export function FactorValuesPanel({
  snapshot,
  selection,
  onSelection,
  compact = false,
}: {
  snapshot: FactorSnapshot;
  selection: FactorSelection;
  onSelection: (value: FactorSelection) => void;
  compact?: boolean;
}) {
  const values = useQuery(
    factorValuesOptions(snapshot.id, selection.offset || 0, selection.listing),
  );
  const validBinding =
    values.data &&
    values.data.snapshot_id === snapshot.id &&
    values.data.manifest_sha256 === snapshot.manifest_sha256 &&
    values.data.output_sha256 === snapshot.output_digest &&
    values.data.data.every(
      (row) => row.factor_version_id === snapshot.factor_version_id,
    );
  return (
    <Panel
      title={compact ? "Actual calculation samples" : "Actual snapshot values"}
    >
      <Metadata
        values={{
          "Full snapshot rows": snapshot.row_count,
          Valid: snapshot.valid_count,
          Missing: snapshot.missing_count,
          Snapshot: snapshot.id,
        }}
      />
      <p className="muted">
        Saved output values, not simulated examples. Null is unavailable, never
        zero. Counts above cover the entire snapshot; table counts cover the
        server query scope.
      </p>
      <Field id="factor-listing-filter" label="Exact listing filter">
        <Input
          id="factor-listing-filter"
          value={selection.listing || ""}
          maxLength={200}
          onChange={(e) =>
            onSelection({
              ...selection,
              listing: e.target.value || undefined,
              date: undefined,
              offset: undefined,
            })
          }
        />
      </Field>
      {values.isPending ? (
        <Loading />
      ) : values.error ? (
        <ErrorState
          error={values.error}
          retry={() => void values.refetch({ cancelRefetch: false })}
        />
      ) : values.data && !validBinding ? (
        <ErrorState
          error={
            new Error(
              "Value evidence does not match the selected version and snapshot hashes.",
            )
          }
        />
      ) : (
        values.data && (
          <>
            <div className="factor-table-wrap">
              <table className="factor-table" data-testid="factor-values">
                <thead>
                  <tr>
                    <th>Session / listing</th>
                    <th>Value / missingness</th>
                    <th>Available at</th>
                    <th>Input window [start,end)</th>
                    <th>Calculation</th>
                  </tr>
                </thead>
                <tbody>
                  {values.data.data.map((row) => (
                    <tr key={`${row.event_time}:${row.listing_id}`}>
                      <td>
                        {row.session_date}
                        <br />
                        {row.listing_id}
                      </td>
                      <td>
                        {factorValue(
                          row.value,
                          row.value_state,
                          row.missing_reason,
                        )}
                      </td>
                      <td>{row.available_at}</td>
                      <td>
                        {row.input_window_start} → {row.input_window_end}
                      </td>
                      <td>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() =>
                            onSelection({
                              ...selection,
                              listing: row.listing_id,
                              date: row.session_date,
                              offset: undefined,
                            })
                          }
                        >
                          Inspect sample
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!values.data.count && (
              <p className="factor-empty">
                No rows match this query. This is not a zero-valued factor.
              </p>
            )}
            <Pagination
              offset={selection.offset || 0}
              limit={50}
              count={values.data.count}
              busy={values.isFetching}
              onPage={(offset) => onSelection({ ...selection, offset })}
            />
            <JsonDetails
              title="Query scope and output identity"
              value={{
                scope: values.data.scope,
                downsampled: values.data.downsampled,
                output_sha256: values.data.output_sha256,
                manifest_sha256: values.data.manifest_sha256,
              }}
            />
          </>
        )
      )}
      {selection.listing && selection.date && (
        <FactorSample
          key={`${snapshot.id}:${selection.listing}:${selection.date}`}
          snapshot={snapshot.id}
          version={snapshot.factor_version_id}
          listing={selection.listing}
          date={selection.date}
        />
      )}
    </Panel>
  );
}
function FactorSample({
  snapshot,
  version,
  listing,
  date,
}: {
  snapshot: string;
  version: string;
  listing: string;
  date: string;
}) {
  const sample = useQuery(factorSampleOptions(snapshot, listing, date));
  const matched =
    sample.data &&
    sample.data.snapshot_id === snapshot &&
    sample.data.listing_id === listing &&
    sample.data.session_date === date &&
    sample.data.samples.every(
      (item) =>
        item.factor_version_id === version &&
        item.output.factor_version_id === version &&
        item.listing_id === listing &&
        item.session_date === date,
    );
  return (
    <section className="mt-5" aria-label="Saved calculation evidence">
      <h3 className="font-semibold">
        Saved calculation evidence · {listing} · {date}
      </h3>
      {sample.isPending ? (
        <Loading />
      ) : sample.error ? (
        <ErrorState
          error={sample.error}
          retry={() => void sample.refetch({ cancelRefetch: false })}
        />
      ) : !matched ? (
        <ErrorState
          error={
            new Error(
              "Sample evidence does not match the exact selected identity.",
            )
          }
        />
      ) : (
        sample.data && (
          <>
            <p className="muted">
              Only persisted inputs and operator evidence are shown. Unavailable
              evidence is not reconstructed from a model explanation.
            </p>
            {sample.data.evidence_status === "unavailable" ? (
              <p className="factor-empty">
                Calculation sample unavailable for this exact observation.
              </p>
            ) : (
              sample.data.samples.map((item, index) => (
                <SavedCalculation key={index} sample={item} />
              ))
            )}
            <JsonDetails
              title="Full saved sample evidence"
              value={sample.data}
            />
          </>
        )
      )}
    </section>
  );
}
export function SavedCalculation({
  sample,
}: {
  sample: FactorCalculationSample;
}) {
  return (
    <article data-testid="factor-sample">
      <Metadata
        values={{
          "Saved operator": sample.operator,
          "Evidence source": sample.explanation_source,
          Output: factorValue(
            sample.output.value,
            sample.output.value_state,
            sample.output.missing_reason,
          ),
          "Available at": sample.output.available_at,
          "Input window [start,end)": `${sample.output.input_window_start} → ${sample.output.input_window_end}`,
        }}
      />
      <div className="factor-table-wrap">
        <table className="factor-table">
          <caption>Actual saved input window</caption>
          <thead>
            <tr>
              <th>Listing</th>
              <th>Session</th>
              <th>Close input</th>
              <th>Input available at</th>
            </tr>
          </thead>
          <tbody>
            {sample.inputs.map((input, index) => (
              <tr key={index}>
                <td>{input.listing_id}</td>
                <td>{input.session_date}</td>
                <td>
                  {input.close === null ? "Missing input" : String(input.close)}
                </td>
                <td>{input.available_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {sample.peer_scores.length > 0 && (
        <div className="factor-table-wrap">
          <table className="factor-table">
            <caption>
              Saved calculation-universe peer scores before rank
            </caption>
            <thead>
              <tr>
                <th>Listing</th>
                <th>Score</th>
              </tr>
            </thead>
            <tbody>
              {sample.peer_scores.map(([listing, score]) => (
                <tr key={listing}>
                  <td>{listing}</td>
                  <td>{score}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </article>
  );
}
export function FactorSnapshotEvidence({
  snapshot,
}: {
  snapshot: FactorSnapshot;
}) {
  const evidence = useQuery(factorSnapshotOptions(snapshot.id));
  return (
    <Panel title="Physical snapshot evidence">
      <Metadata
        values={{
          Snapshot: snapshot.id,
          Dataset: snapshot.dataset_version_id,
          "Input SHA-256": snapshot.input_digest,
          "Output SHA-256": snapshot.output_digest,
          "Manifest SHA-256": snapshot.manifest_sha256,
        }}
      />
      <JsonDetails
        title="Server-derived local verification"
        value={snapshot.local_verification}
      />
      <JsonDetails
        title="Source claims (not local verification)"
        value={snapshot.source_claims}
      />
      {evidence.isPending ? (
        <Loading />
      ) : evidence.error ? (
        <ErrorState
          error={evidence.error}
          retry={() => void evidence.refetch({ cancelRefetch: false })}
        />
      ) : evidence.data &&
        (evidence.data.id !== snapshot.id ||
          evidence.data.factor_version_id !== snapshot.factor_version_id ||
          evidence.data.manifest_sha256 !== snapshot.manifest_sha256 ||
          evidence.data.output_digest !== snapshot.output_digest ||
          evidence.data.input_digest !== snapshot.input_digest) ? (
        <ErrorState
          error={
            new Error(
              "Manifest evidence does not match the selected exact snapshot.",
            )
          }
        />
      ) : (
        <JsonDetails
          title="Verified manifest and full evidence"
          value={evidence.data}
        />
      )}
    </Panel>
  );
}
export function FactorEvaluationPanel({ detail }: { detail: FactorDetail }) {
  return (
    <Panel title="Research evaluation">
      <p className="notice">
        Direct-factor analysis, native prediction analysis, and portfolio
        analysis have different meanings. No Qlib metric is inferred from
        materialized values.
      </p>
      {!detail.evaluations.length ? (
        <p className="factor-empty">
          Not evaluated. IC, Rank IC, grouped returns and controlled strategy
          increment are unavailable.
        </p>
      ) : (
        detail.evaluations.map((item, index) => (
          <article className="mt-4" key={String(item.id || index)}>
            <h3>
              {String(item.kind).replaceAll("_", " ")} · {String(item.status)}
            </h3>
            <JsonDetails
              title="Exact evaluation protocol and result"
              value={item}
            />
          </article>
        ))
      )}
      <p className="muted">
        Cross-sectional statistics are not automatically applicable to a
        single-listing time series. Analysis requires a fixed label, horizon,
        universe, window and missing-value protocol.
      </p>
    </Panel>
  );
}
export function FactorUsagePanel({ usage }: { usage: FactorUsage }) {
  return (
    <Panel title="Strategy usage evidence">
      <Metadata
        values={{
          "Declared dependencies": usage.declared,
          "Explicit host-recorded reads": usage.explicit_read,
          "Controlled experiments": usage.experiment,
          "Observation coverage": usage.observation_coverage,
        }}
      />
      <p className="factor-empty">
        No strategy usage is inferred from saving, materializing, exporting, or
        bundling this factor.
      </p>
      <p>
        A declared dependency proves availability only. Explicit reads do not
        prove causal contribution. A consumed stage result is not, by itself, a
        controlled increment experiment.
      </p>
    </Panel>
  );
}

type ComparisonDefinition = {
  left_definition: Record<string, unknown>;
  right_definition: Record<string, unknown>;
  left_code: string;
  right_code: string;
  observation_alignment: { status: string; reasons: string[] };
  unit_scale_comparability: { status: string; reasons: string[] };
  controlled_protocol_comparability: { status: string; reasons: string[] };
  delta_reason?: string;
};
type Comparison = {
  id: string;
  definition: ComparisonDefinition;
  content_sha256: string;
};
export function FactorEvolutionPanel({
  detail,
  versions,
  snapshot,
  owner,
}: {
  detail: FactorDetail;
  versions: FactorVersion[];
  snapshot?: FactorSnapshot;
  owner?: string;
}) {
  const [leftVersion, setLeftVersion] = useState(
    detail.version.parent_version_id || "",
  );
  const [leftSnapshot, setLeftSnapshot] = useState("");
  const [result, setResult] = useState<Comparison>();
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const active = useRef(false);
  const left = useQuery({
    ...factorDetailOptions(detail.version.factor_id, leftVersion),
    enabled: Boolean(leftVersion),
  });
  const inputIdentity = `${leftVersion}:${leftSnapshot}:${snapshot?.id || ""}`;
  const [resultIdentity, setResultIdentity] = useState("");
  async function compare() {
    if (!owner || active.current || !leftVersion) return;
    active.current = true;
    setBusy(true);
    setError(undefined);
    const identity = inputIdentity;
    const body = {
      left: { version_id: leftVersion, snapshot_id: leftSnapshot || null },
      right: {
        version_id: detail.version.id,
        snapshot_id: snapshot?.id || null,
      },
    };
    try {
      const comparison = await factorRequest<Comparison>(
        "/factor-comparisons",
        { method: "POST", body, key: submissionKey(owner, "compare", body) },
      );
      setResult(comparison);
      setResultIdentity(identity);
    } catch (err) {
      setError(err);
    } finally {
      active.current = false;
      setBusy(false);
    }
  }
  return (
    <Panel title="Evolution and bilateral comparison">
      <ol>
        {versions.map((version) => (
          <li className="mb-2" key={version.id}>
            <Link
              to="/factors/$factorId/versions/$versionId"
              params={{ factorId: version.factor_id, versionId: version.id }}
              search={{ tab: "evolution" }}
              className="text-link"
            >
              v{version.ordinal} · {version.source_kind}
            </Link>{" "}
            · parent {version.parent_version_id || "none"}
          </li>
        ))}
      </ol>
      <a className="text-link" href={seededPreflightHref(detail.version.id)}>
        Use this exact factor version as a configured-model parent
      </a>
      <p>
        Opens a new preflight with an explicit parent reference. It does not
        submit, approve a proposal or change this version. Model and steer
        choices still require explicit selection.
      </p>
      <p className="muted">
        Lineage is the recorded parent relation, not chronological adjacency.
        Failed and unmaterialized definitions remain visible.
      </p>
      <div className="factor-toolbar">
        <Field id="factor-comparison-left" label="Left version">
          <select
            id="factor-comparison-left"
            disabled={busy}
            value={leftVersion}
            onChange={(e) => {
              setLeftVersion(e.target.value);
              setLeftSnapshot("");
              setError(undefined);
            }}
          >
            <option value="">Choose a version</option>
            {versions
              .filter((v) => v.id !== detail.version.id)
              .map((v) => (
                <option key={v.id} value={v.id}>
                  v{v.ordinal}
                </option>
              ))}
          </select>
        </Field>
        <Field id="factor-comparison-snapshot" label="Left exact snapshot">
          <select
            id="factor-comparison-snapshot"
            disabled={busy || !left.data}
            value={leftSnapshot}
            onChange={(e) => setLeftSnapshot(e.target.value)}
          >
            <option value="">Definition only</option>
            {left.data?.snapshots.map((s) => (
              <option key={s.id} value={s.id}>
                {s.id.slice(0, 8)} · {s.dataset_version_id.slice(0, 8)}
              </option>
            ))}
          </select>
        </Field>
        <Button
          onClick={() => void compare()}
          disabled={busy || !owner || !left.data}
        >
          {busy ? "Comparing…" : "Compare exact versions"}
        </Button>
      </div>
      <p>
        Right: v{detail.version.ordinal},{" "}
        {snapshot ? `snapshot ${snapshot.id}` : "definition only"}.
      </p>
      {left.error && <ErrorState error={left.error} />}
      {error !== undefined && <ErrorState error={error} />}
      {result && resultIdentity === inputIdentity && (
        <ComparisonEvidence result={result} />
      )}
    </Panel>
  );
}
export function ComparisonEvidence({ result }: { result: Comparison }) {
  return (
    <section className="mt-5" aria-label="Factor comparison result">
      <h3 className="font-semibold">Exact comparison evidence</h3>
      <div className="factor-grid">
        {(
          [
            "observation_alignment",
            "unit_scale_comparability",
            "controlled_protocol_comparability",
          ] as const
        ).map((kind) => (
          <article key={kind}>
            <h3>{kind.replaceAll("_", " ")}</h3>
            <p>{result.definition[kind].status.replaceAll("_", " ")}</p>
            <ul>
              {result.definition[kind].reasons.map((reason) => (
                <li key={reason}>{reason.replaceAll("_", " ")}</li>
              ))}
            </ul>
          </article>
        ))}
      </div>
      <p className="notice">
        Aligned observations are not proof of compatible units or causal
        strategy improvement. Incompatible subtraction remains unavailable.
      </p>
      <div className="factor-grid">
        <article>
          <h3>Left source</h3>
          <pre className="factor-code" tabIndex={0}>
            {result.definition.left_code}
          </pre>
          <JsonDetails
            title="Left definition"
            value={result.definition.left_definition}
          />
        </article>
        <article>
          <h3>Right source</h3>
          <pre className="factor-code" tabIndex={0}>
            {result.definition.right_code}
          </pre>
          <JsonDetails
            title="Right definition"
            value={result.definition.right_definition}
          />
        </article>
      </div>
      <BilateralValues key={result.id} comparison={result.id} />
      <JsonDetails
        title="Comparison identity"
        value={{ id: result.id, content_sha256: result.content_sha256 }}
      />
    </section>
  );
}
function BilateralValues({ comparison }: { comparison: string }) {
  const [offset, setOffset] = useState(0);
  const values = useQuery(factorComparisonValuesOptions(comparison, offset));
  if (values.isPending) return <Loading />;
  if (values.error)
    return (
      <ErrorState
        error={values.error}
        retry={() => void values.refetch({ cancelRefetch: false })}
      />
    );
  if (!values.data) return null;
  if (values.data.comparison_id !== comparison)
    return (
      <ErrorState
        error={new Error("Bilateral values do not match this comparison.")}
      />
    );
  return (
    <>
      <h3 className="font-semibold mt-4">Actual bilateral observations</h3>
      <p className="muted">
        Scope: {values.data.scope.replaceAll("_", " ")}. Each side retains its
        own missingness and availability. No interpolation or missing-to-zero
        conversion.
      </p>
      <div className="factor-table-wrap">
        <table className="factor-table" data-testid="factor-comparison-values">
          <thead>
            <tr>
              <th>Session / listing</th>
              <th>Left value</th>
              <th>Left available</th>
              <th>Right value</th>
              <th>Right available</th>
              <th>Right minus left</th>
            </tr>
          </thead>
          <tbody>
            {values.data.data.map((row) => (
              <tr key={`${row.event_time}:${row.listing_id}`}>
                <td>
                  {row.session_date}
                  <br />
                  {row.listing_id}
                </td>
                <td>
                  {row.left
                    ? factorValue(
                        row.left.value,
                        row.left.value_state,
                        row.left.missing_reason,
                      )
                    : "Absent observation"}
                </td>
                <td>{row.left?.available_at || "Unavailable"}</td>
                <td>
                  {row.right
                    ? factorValue(
                        row.right.value,
                        row.right.value_state,
                        row.right.missing_reason,
                      )
                    : "Absent observation"}
                </td>
                <td>{row.right?.available_at || "Unavailable"}</td>
                <td>
                  {row.delta === null
                    ? `Unavailable · ${(row.delta_reason || "no comparable delta").replaceAll("_", " ")}`
                    : String(row.delta)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!values.data.count && (
        <p className="factor-empty">
          No paired observation evidence under these bindings. Definition-only
          comparison remains available.
        </p>
      )}
      <Pagination
        count={values.data.count}
        offset={offset}
        limit={50}
        busy={values.isFetching}
        onPage={setOffset}
      />
    </>
  );
}
export function FactorExportPanel({
  version,
  snapshot,
  owner,
}: {
  version: FactorVersion;
  snapshot?: FactorSnapshot;
  owner: string;
}) {
  const [includeValues, setIncludeValues] = useState(false);
  const [bundleName, setBundleName] = useState("");
  const [result, setResult] = useState<unknown>();
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const active = useRef(false);
  async function perform(kind: "bundle" | "export") {
    if (active.current) return;
    active.current = true;
    setBusy(true);
    setError(undefined);
    setResult(undefined);
    try {
      const body =
        kind === "bundle"
          ? {
              name: bundleName,
              members: [
                {
                  factor_version_id: version.id,
                  definition_sha256: version.definition_sha256,
                },
              ],
              usage_constraints: {},
            }
          : {
              snapshot_ids: snapshot ? [snapshot.id] : [],
              evaluation_ids: [],
              include_values: Boolean(snapshot && includeValues),
            };
      const response = await factorRequest<Record<string, unknown>>(
        kind === "bundle"
          ? "/factor-bundles"
          : `/factor-versions/${version.id}/exports`,
        {
          method: "POST",
          body,
          key: submissionKey(owner, `${kind}.${version.id}`, body),
        },
      );
      setResult(response);
      if (kind === "export") {
        if (typeof response.id !== "string")
          throw new Error(
            "Export returned no package identity; no download was attempted.",
          );
        const file = await factorRequest<Blob>(
          `/factor-packages/${encodeURIComponent(response.id)}/download`,
          { blob: true },
        );
        const url = URL.createObjectURL(file);
        const a = document.createElement("a");
        a.href = url;
        a.download = `factor-${version.id}.zip`;
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
    } catch (err) {
      setError(err);
    } finally {
      active.current = false;
      setBusy(false);
    }
  }
  return (
    <Panel title="Export and fixed bundle">
      <p>
        All actions pin v{version.ordinal} and its exact definition hash.
        Bundles declare availability, not strategy use or a latest-snapshot
        binding.
      </p>
      <div className="factor-toolbar">
        <label>
          <input
            type="checkbox"
            checked={Boolean(snapshot && includeValues)}
            disabled={!snapshot || busy}
            onChange={(e) => setIncludeValues(e.target.checked)}
          />{" "}
          Include selected snapshot values
        </label>
        <Button disabled={busy} onClick={() => void perform("export")}>
          Export exact factor package
        </Button>
      </div>
      <p className="muted">
        {snapshot
          ? `Selected snapshot: ${snapshot.id}`
          : "Definition-only export. Select a snapshot to include values."}
      </p>
      <div className="factor-toolbar">
        <Field id="factor-bundle-name" label="Fixed bundle name">
          <Input
            id="factor-bundle-name"
            maxLength={240}
            value={bundleName}
            onChange={(e) => setBundleName(e.target.value)}
            disabled={busy}
          />
        </Field>
        <Button
          variant="outline"
          disabled={busy || !bundleName.trim()}
          onClick={() => void perform("bundle")}
        >
          Save selected version as bundle
        </Button>
      </div>
      {error !== undefined && <ErrorState error={error} />}
      {result !== undefined && (
        <>
          <p role="status">Immutable registration saved.</p>
          <JsonDetails
            title="Saved registration and exact bindings"
            value={result}
          />
        </>
      )}
    </Panel>
  );
}
