import { deploymentUrl } from "@/lib/deployment";
import { EngineInputs } from "./engine-inputs";
import { Link, useParams, useSearch } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { ErrorState, Loading, PageHeader, Panel } from "@/components/common";
import {
  engineCapabilitiesOptions,
  engineDetailOptions,
  engineHistoryOptions,
  engineRunsOptions,
} from "@/lib/engine-runs-api";
import { EngineControls } from "./engine-controls";
import { engineRunActive } from "@/lib/engine-submission-api";
import { formatDate } from "@/lib/format";

const scopeNotice =
  "Persisted research metadata only. Source publication and evaluation tickets do not prove a completed factor snapshot or accepted production execution.";

export function EngineHistoryPage() {
  const { offset } = useSearch({ from: "/workspace/engine-runs" });
  const capabilities = useQuery(engineCapabilitiesOptions);
  const runs = useQuery(engineRunsOptions(offset));
  return (
    <div className="page">
      <PageHeader
        title="Engine research history"
        subtitle="Read saved RD-Agent research evidence and check server-authorized entry availability."
      />
      <a href={deploymentUrl("/rd-steers")}>
        Manage user-provided RD steer inputs
      </a>
      {capabilities.isPending ? (
        <Loading />
      ) : capabilities.error ? (
        <ErrorState
          error={capabilities.error}
          retry={() => void capabilities.refetch()}
        />
      ) : (
        <Panel title="Availability">
          {capabilities.data.engines.map((engine) => (
            <p key={engine.engine}>
              <strong>
                {engine.engine === "rdagent"
                  ? "RD-Agent"
                  : engine.engine === "openevolve"
                    ? "OpenEvolve"
                    : engine.engine}
              </strong>
              :{" "}
              {engine.can_read_history
                ? "Saved history available"
                : "History unavailable"}
              .{" "}
              {engine.can_create
                ? "Configured submission available"
                : "Creation disabled"}{" "}
              — <code>{engine.reason_code}</code>.
              {engine.engine === "rdagent" && (
                <span>
                  {" "}
                  The preceding entry is the maintained fixture.
                  Configured-model generation:{" "}
                  {engine.configured_model_can_create
                    ? "available for an explicitly selected authorized model"
                    : "unavailable"}{" "}
                  —{" "}
                  <code>
                    {engine.configured_model_reason_code ??
                      "rd_model_unconfigured"}
                  </code>
                  . Model billing remains unknown.
                </span>
              )}
            </p>
          ))}
          {capabilities.data.engines.some(
            (engine) => engine.engine === "rdagent" && engine.can_preflight,
          ) && (
            <Link to="/engine-runs/preflight" className="text-link">
              Check RD-Agent dataset eligibility
            </Link>
          )}
          <p className="muted">{scopeNotice}</p>
        </Panel>
      )}
      {runs.isPending ? (
        <Loading />
      ) : runs.error ? (
        <ErrorState error={runs.error} retry={() => void runs.refetch()} />
      ) : (
        <Panel title="Saved RD-Agent runs">
          {!runs.data.data.length && (
            <p>
              No persisted RD-Agent runs on this page. No run has been created
              by visiting this screen.
            </p>
          )}
          {runs.data.data.map((run) => (
            <article key={run.id} className="mb-4">
              <Link
                to="/engine-runs/$runId"
                params={{ runId: run.id }}
                search={{ offset: 0 }}
                className="text-link"
              >
                {run.task_name}
              </Link>
              <p>
                {run.engine} · {run.status} · Generation {run.generation}
              </p>
              <p className="muted">
                {run.id} · {formatDate(run.created_at)}
              </p>
            </article>
          ))}
          {offset > 0 && (
            <Link
              to="/engine-runs"
              search={{ offset: Math.max(0, offset - 25) }}
            >
              Previous page
            </Link>
          )}{" "}
          {offset + 25 < runs.data.count && (
            <Link to="/engine-runs" search={{ offset: offset + 25 }}>
              Next page
            </Link>
          )}
        </Panel>
      )}
    </div>
  );
}

export function EngineHistoryDetailPage() {
  const { runId } = useParams({ from: "/workspace/engine-runs/$runId" });
  const { offset } = useSearch({ from: "/workspace/engine-runs/$runId" });
  const run = useQuery({
    ...engineDetailOptions(runId),
    refetchInterval: (query) =>
      query.state.data && engineRunActive(query.state.data.status)
        ? 2000
        : false,
  });
  const history = useQuery({
    ...engineHistoryOptions(runId, offset),
    enabled: run.isSuccess,
    refetchInterval:
      run.data && engineRunActive(run.data.status) ? 2000 : false,
  });
  if (run.isPending) return <Loading />;
  if (run.error)
    return <ErrorState error={run.error} retry={() => void run.refetch()} />;
  return (
    <div className="page">
      <Link to="/engine-runs" search={{ offset: 0 }} className="text-link">
        All engine history
      </Link>
      <PageHeader
        title={run.data.task_name}
        subtitle="RD-Agent · saved research history"
      />
      <EngineInputs key={runId} runId={runId} />
      <Panel title="Immutable run identity">
        <p>
          {run.data.status} · Generation {run.data.generation} · {run.data.id}
        </p>
        <p>{run.data.task_description}</p>
        <p className="muted">{scopeNotice}</p>
        <p>Dataset: {run.data.dataset_version_id}</p>
        <p className="break-all">Config SHA-256: {run.data.config_sha256}</p>
        <p>Strategy: not applicable to factor research</p>
        <EngineControls key={runId} run={run.data} reload={run.refetch} />
        {run.data.error && <p role="alert">{run.data.error}</p>}
        <details>
          <summary>Declared budget limits (not observed usage)</summary>
          <pre>{JSON.stringify(run.data.budget_limits, null, 2)}</pre>
        </details>
      </Panel>
      {history.isPending ? (
        <Loading />
      ) : history.error ? (
        <ErrorState
          error={history.error}
          retry={() => void history.refetch()}
        />
      ) : (
        <Panel title="Generation and evaluation history">
          {!history.data.data.length && (
            <p>No generation records have been persisted.</p>
          )}
          {history.data.data.map((item) => (
            <article key={item.id} className="mb-6">
              <h3>
                Generation {item.generation} · Step {item.logical_step}
              </h3>
              <p>
                Generation outcome:{" "}
                {item.generation_outcome ?? "No outcome recorded"}
              </p>
              <p>
                Evaluation:{" "}
                {item.evaluation_status ??
                  (item.evaluation_id
                    ? "Ticket recorded; no outcome"
                    : "Not recorded")}
              </p>
              <p>
                Legacy feature snapshot: {item.snapshot_id ?? "Not recorded"}
              </p>
              <p>
                Evaluation protocol:{" "}
                {item.evaluation_protocol ?? "No result claim recorded"}
              </p>
              <p>
                Execution snapshot:{" "}
                {item.execution_snapshot_id ??
                  (item.execution_snapshot_publication_id
                    ? "Publication pending; no completed snapshot"
                    : "Not recorded")}
              </p>
              {(item.execution_snapshot_id ||
                item.execution_snapshot_publication_id) && (
                <p>
                  <Link
                    to="/engine-runs/$runId/snapshots/$snapshotId"
                    params={{
                      runId,
                      snapshotId: (item.execution_snapshot_id ||
                        item.execution_snapshot_publication_id)!,
                    }}
                    className="text-link"
                  >
                    Inspect execution snapshot metadata
                  </Link>
                </p>
              )}
              {item.execution_snapshot_id && (
                <p className="break-all">
                  {item.execution_snapshot_schema} ·{" "}
                  {item.execution_snapshot_state} · Manifest SHA-256:{" "}
                  {item.execution_snapshot_manifest_sha256}
                </p>
              )}
              {item.error_code && <p>{item.error_code}</p>}
              {item.factor_id && item.factor_version_id && (
                <Link
                  to="/factors/$factorId/versions/$versionId"
                  params={{
                    factorId: item.factor_id,
                    versionId: item.factor_version_id,
                  }}
                  className="text-link"
                >
                  Inspect persisted factor version
                </Link>
              )}
              <dl className="break-all">
                <dt>Source</dt>
                <dd>{item.source_id ?? "Not recorded"}</dd>
                <dt>Source SHA-256</dt>
                <dd>{item.source_sha256 ?? "Not recorded"}</dd>
                <dt>Evaluated source</dt>
                <dd>{item.evaluated_source_id ?? "Not recorded"}</dd>
                <dt>Adopted source</dt>
                <dd>{item.adopted_source_id ?? "Not recorded"}</dd>
                <dt>Continuation source</dt>
                <dd>{item.continuation_source_id ?? "Not recorded"}</dd>
              </dl>
            </article>
          ))}
          {offset > 0 && (
            <Link
              to="/engine-runs/$runId"
              params={{ runId }}
              search={{ offset: Math.max(0, offset - 25) }}
            >
              Previous page
            </Link>
          )}{" "}
          {offset + 25 < history.data.count && (
            <Link
              to="/engine-runs/$runId"
              params={{ runId }}
              search={{ offset: offset + 25 }}
            >
              Next page
            </Link>
          )}
        </Panel>
      )}
    </div>
  );
}
