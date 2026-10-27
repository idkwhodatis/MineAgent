import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ErrorState, Loading, Panel } from "@/components/common";
import {
  exactFactorExecutionOptions,
  factorExecutionOptions,
  type FactorExecutionRecord,
} from "@/lib/factor-execution-api";
import {
  executionSnapshotOptions,
  engineDetailOptions,
} from "@/lib/engine-runs-api";
import type { FactorVersion } from "@/lib/factor-api";
import { DirectAnalysisPanel, ReleasedSnapshotPanel } from "../engine-results";
import type { FactorSelection } from "./selection";

export function ExecutionPicker({
  factorId,
  versionId,
  value,
  select,
  label = "Exact research execution snapshot",
}: {
  factorId: string;
  versionId: string;
  value?: string;
  select: (id?: string) => void;
  label?: string;
}) {
  const [offset, setOffset] = useState(0);
  const records = useQuery(factorExecutionOptions(factorId, versionId, offset));
  return (
    <Panel title={label}>
      <p>
        Research execution snapshots are distinct from legacy provider
        snapshots. Selecting one does not release values or metrics.
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
          <select
            aria-label={label}
            value={value || ""}
            onChange={(event) => select(event.target.value || undefined)}
          >
            <option value="">No research execution selected</option>
            {value &&
              !records.data.data.some(
                (row) => row.execution_snapshot_id === value,
              ) && (
                <option value={value}>
                  Selected exact snapshot (another metadata page)
                </option>
              )}
            {records.data.data
              .filter(
                (row) =>
                  row.factor_id === factorId &&
                  row.factor_version_id === versionId &&
                  row.kind === "execution_snapshot",
              )
              .map((row) => (
                <option
                  key={row.execution_snapshot_id}
                  value={row.execution_snapshot_id}
                >
                  {row.created_at} · {row.publication_state} ·{" "}
                  {row.execution_snapshot_id}
                </option>
              ))}
          </select>
          <button
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - 20))}
          >
            Previous execution records
          </button>
          <button
            disabled={offset + 20 >= records.data.count}
            onClick={() => setOffset(offset + 20)}
          >
            Next execution records
          </button>
        </>
      )}
    </Panel>
  );
}

export function ExecutionTabContent({
  version,
  selection,
  versions,
  changeSelection,
}: {
  version: FactorVersion;
  selection: FactorSelection;
  versions: FactorVersion[];
  changeSelection: (next: FactorSelection) => void;
}) {
  const tab = selection.tab || "overview";
  if (selection.invalidExecution)
    return (
      <ErrorState
        error={
          new Error(
            "Malformed or mixed legacy/research snapshot selection. Select one exact snapshot family.",
          )
        }
      />
    );
  if (!selection.execution) return null;
  if (
    !["overview", "values", "evaluation", "evolution", "evidence"].includes(tab)
  )
    return null;
  const other = versions.find((row) => row.id === selection.compareVersion);
  return (
    <>
      <ExecutionVersionResult
        version={version}
        snapshotId={selection.execution}
        view={tab === "evaluation" ? "evaluation" : "values"}
      />
      {tab === "evolution" && (
        <Panel title="Compare exact research execution evidence">
          <p>
            This is a side-by-side view of independently released retained
            results, not a new evaluation or an equal-protocol performance
            claim. Definition/code comparison remains below. Different windows,
            datasets or protocols must not be treated as controlled gain.
          </p>
          <select
            aria-label="Other factor version for execution evidence"
            value={selection.compareVersion || ""}
            onChange={(event) =>
              changeSelection({
                ...selection,
                compareVersion: event.target.value || undefined,
                compareExecution: undefined,
              })
            }
          >
            <option value="">Choose exact other version</option>
            {versions
              .filter((row) => row.id !== version.id)
              .map((row) => (
                <option key={row.id} value={row.id}>
                  v{row.ordinal} · {row.id}
                  {row.id === version.parent_version_id
                    ? " · recorded parent"
                    : ""}
                </option>
              ))}
          </select>
          {selection.compareVersion && !other && (
            <p role="alert">
              Other version is not in the loaded version page. No replacement
              was selected.
            </p>
          )}
          {other && (
            <>
              <ExecutionPicker
                key={other.id}
                factorId={other.factor_id}
                versionId={other.id}
                value={selection.compareExecution}
                label="Other version execution snapshot"
                select={(id) =>
                  changeSelection({ ...selection, compareExecution: id })
                }
              />
              {selection.compareExecution && (
                <ExecutionVersionResult
                  key={`${other.id}:${selection.compareExecution}`}
                  version={other}
                  snapshotId={selection.compareExecution}
                  view="values"
                />
              )}
            </>
          )}
        </Panel>
      )}
    </>
  );
}

export function ExecutionVersionResult({
  version,
  snapshotId,
  view,
}: {
  version: FactorVersion;
  snapshotId: string;
  view: "values" | "evaluation";
}) {
  const record = useQuery(
    exactFactorExecutionOptions(version.factor_id, version.id, snapshotId),
  );
  if (record.isPending) return <Loading />;
  if (record.error)
    return (
      <ErrorState
        error={record.error}
        retry={() => void record.refetch({ cancelRefetch: false })}
      />
    );
  if (
    !record.data ||
    record.data.factor_id !== version.factor_id ||
    record.data.factor_version_id !== version.id ||
    record.data.execution_snapshot_id !== snapshotId
  )
    return (
      <ErrorState
        error={
          new Error(
            "Execution selection does not match the immutable factor version.",
          )
        }
      />
    );
  return (
    <ExecutionRecordResult version={version} record={record.data} view={view} />
  );
}
function ExecutionRecordResult({
  version,
  record,
  view,
}: {
  version: FactorVersion;
  record: FactorExecutionRecord;
  view: "values" | "evaluation";
}) {
  const metadata = useQuery(
    executionSnapshotOptions(record.run_id, record.execution_snapshot_id),
  );
  const declaredRun = useQuery(engineDetailOptions(record.run_id));
  if (metadata.isPending) return <Loading />;
  if (metadata.error)
    return (
      <ErrorState
        error={metadata.error}
        retry={() => void metadata.refetch({ cancelRefetch: false })}
      />
    );
  const data = metadata.data;
  if (
    data.id !== record.execution_snapshot_id ||
    data.run_id !== record.run_id ||
    data.factor_version_id !== version.id ||
    data.evaluation_id !== record.evaluation_id
  )
    return (
      <ErrorState
        error={
          new Error(
            "Snapshot metadata does not match the selected execution and factor version.",
          )
        }
      />
    );
  const definition = version.definition;
  return (
    <section
      aria-label={`Research execution evidence for version ${version.ordinal}`}
    >
      {declaredRun.data?.id === record.run_id && (
        <Panel title="Declared research question (not execution proof)">
          <h3>{declaredRun.data.task_name}</h3>
          <p>{declaredRun.data.task_description}</p>
          <p>
            The retained source and released output below remain authoritative;
            a task description is not a verified symbolic formula.
          </p>
        </Panel>
      )}
      <Panel
        title={`Version ${version.ordinal} · retained research calculation`}
      >
        <p>
          Exact version {version.id}; source {version.source_kind}.{" "}
          {String(definition.factor_kind).replaceAll("_", " ")} output in{" "}
          {String(definition.output_unit)}; interpretation{" "}
          {String(definition.interpretation ?? "unspecified").replaceAll(
            "_",
            " ",
          )}
          .
        </p>
        <p>
          Declared calculation input:{" "}
          {Array.isArray(definition.input_fields)
            ? definition.input_fields
                .map((field) =>
                  typeof field === "object" && field !== null && "name" in field
                    ? String(field.name)
                    : "unspecified",
                )
                .join(", ")
            : "unspecified"}
          . Lookback {String(definition.lookback_sessions)} prior sessions; lag{" "}
          {String(definition.lag_sessions ?? 0)} sessions.
        </p>
        <p>
          Output window: {data.window_start} ≤ session &lt; {data.window_end}.
          Dataset {data.dataset_version_id}.
        </p>
        <p>
          Formula and internal steps are not inferred from arbitrary Python. The
          exact retained source below defines the computation; this endpoint
          provides output rows and availability, not verified intermediate
          operands. Declared daily availability is not point-in-time
          certification.
        </p>
        <details>
          <summary>Exact formula implementation and commitments</summary>
          <pre>{version.code}</pre>
          <p>Code SHA-256: {version.code_sha256}</p>
          <p>Input SHA-256: {data.input_sha256}</p>
          <p>Output SHA-256: {data.output_sha256 ?? "No completed output"}</p>
        </details>
      </Panel>
      {data.state !== "completed" ? (
        <p>
          Publication is pending. No result or successful calculation is
          implied.
        </p>
      ) : view === "evaluation" ? (
        record.analysis_state === "result_recorded" && record.analysis_id ? (
          <DirectAnalysisPanel
            runId={record.run_id}
            analysisId={record.analysis_id}
            expectedSnapshotId={record.execution_snapshot_id}
          />
        ) : (
          <p>
            {record.analysis_state === "pending"
              ? "Direct analysis declared; result not recorded."
              : "No direct analysis declared for this exact execution."}
          </p>
        )
      ) : (
        <ReleasedSnapshotPanel
          key={record.execution_snapshot_id}
          runId={record.run_id}
          snapshotId={record.execution_snapshot_id}
        />
      )}
    </section>
  );
}
