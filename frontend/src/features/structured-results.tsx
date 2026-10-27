import { useState, type ReactNode } from "react";
import { Link, useParams, useSearch } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { ErrorState, Loading, PageHeader, Panel } from "@/components/common";
import { deploymentUrl } from "@/lib/deployment";
import {
  factorStructuredOptions,
  releaseStructuredExecution,
  structuredCapabilityOptions,
  structuredComparisonOptions,
  structuredExecutionOptions,
  structuredRecordsOptions,
  structuredReleaseRequired,
} from "@/lib/structured-results-api";
import {
  StructuredComparisonPanel,
  StructuredExecutionPanel,
} from "./structured-comparison";

function ReadCapability({ children }: { children: ReactNode }) {
  const capability = useQuery(structuredCapabilityOptions);
  if (capability.isPending) return <Loading />;
  if (capability.error)
    return (
      <ErrorState
        error={capability.error}
        retry={() => void capability.refetch()}
      />
    );
  if (!capability.data.can_read)
    return (
      <Panel title="Structured history unavailable">
        <p>{capability.data.reason_code}</p>
        <p>No new execution or legacy seed fallback is enabled.</p>
      </Panel>
    );
  return <>{children}</>;
}
export function FactorStructuredExecutions({
  factorId,
  versionId,
}: {
  factorId: string;
  versionId: string;
}) {
  return (
    <ReadCapability>
      <FactorStructuredRecords factorId={factorId} versionId={versionId} />
    </ReadCapability>
  );
}
function FactorStructuredRecords({
  factorId,
  versionId,
}: {
  factorId: string;
  versionId: string;
}) {
  const [offset, setOffset] = useState(0);
  const records = useQuery(
    factorStructuredOptions(factorId, versionId, offset),
  );
  return (
    <Panel title="Observed structured strategy executions">
      <p>
        Recorded execution identity is not a release of factor values, trading
        outcomes or comparison flags. These closed-program executions are not
        OpenEvolve factor search.
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
            <p>No retained structured execution records on this page.</p>
          )}
          <ul>
            {records.data.data.map((row) =>
              row.factor_id !== factorId ||
              row.factor_version_id !== versionId ? (
                <li key={row.execution_id} role="alert">
                  Execution identity differs from selected factor version.
                </li>
              ) : (
                <li key={row.execution_id}>
                  <Link
                    to="/engine-runs/$runId/structured-executions/$executionId"
                    params={{
                      runId: row.run_id,
                      executionId: row.execution_id,
                    }}
                  >
                    Open observed strategy execution {row.execution_id}
                  </Link>
                </li>
              ),
            )}
          </ul>
          <button
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - 50))}
          >
            Previous structured records
          </button>
          <button
            disabled={records.data.data.length < 50}
            onClick={() => setOffset(offset + 50)}
          >
            Next structured records
          </button>
        </>
      )}
    </Panel>
  );
}
export function StructuredExecutionPage() {
  const { runId, executionId } = useParams({
    from: "/workspace/engine-runs/$runId/structured-executions/$executionId",
  });
  return (
    <ReadCapability>
      <StructuredExecutionContent
        key={`${runId}:${executionId}`}
        runId={runId}
        executionId={executionId}
      />
    </ReadCapability>
  );
}
function StructuredExecutionContent({
  runId,
  executionId,
}: {
  runId: string;
  executionId: string;
}) {
  const records = useQuery(structuredRecordsOptions(runId));
  if (records.isPending) return <Loading />;
  if (records.error)
    return (
      <ErrorState error={records.error} retry={() => void records.refetch()} />
    );
  const selected = records.data.data.find(
    (row) => row.run_id === runId && row.execution_id === executionId,
  );
  if (!selected)
    return (
      <ErrorState
        error={
          new Error(
            "Exact owned execution is unavailable; no newer record was substituted.",
          )
        }
      />
    );
  return (
    <div className="page">
      <PageHeader
        title="Observed structured execution"
        subtitle="Retained closed operator stages and native accounting, not arbitrary strategy causality or new execution authority."
      />
      <Link
        to="/factors/$factorId/versions/$versionId"
        params={{
          factorId: selected.factor_id,
          versionId: selected.factor_version_id,
        }}
        search={{ tab: "usage", execution: selected.execution_snapshot_id }}
      >
        Back to exact factor version
      </Link>
      {records.data.data
        .filter((row) => row.execution_id !== executionId)
        .map((row) => (
          <p key={row.execution_id}>
            <Link
              to="/engine-runs/$runId/structured-comparison"
              params={{ runId }}
              search={{ before: executionId, after: row.execution_id }}
            >
              Compare with exact factor version {row.factor_version_id}
            </Link>
          </p>
        ))}
      <SingleExecutionResult runId={runId} executionId={executionId} />
    </div>
  );
}
function SingleExecutionResult({
  runId,
  executionId,
}: {
  runId: string;
  executionId: string;
}) {
  const result = useQuery(structuredExecutionOptions(runId, executionId));
  const [busy, setBusy] = useState(false),
    [error, setError] = useState<unknown>(null);
  async function release() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await releaseStructuredExecution(runId, executionId);
      await result.refetch();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  if (result.isPending) return <Loading />;
  if (result.error)
    return structuredReleaseRequired(result.error) ? (
      <Panel title="Explicit structured result release required">
        <p>
          Release records full retained factor and market data exposure,
          including history and forward-label extent. Labels are not trading
          inputs. No outcomes are shown before commit.
        </p>
        <button disabled={busy} onClick={() => void release()}>
          Release exact structured execution
        </button>
        {error != null && <ErrorState error={error} />}
      </Panel>
    ) : (
      <ErrorState error={result.error} retry={() => void result.refetch()} />
    );
  if (
    result.data.schema_version !== "mineagent.structured-execution-view.v1" ||
    result.data.run_id !== runId ||
    result.data.execution_id !== executionId
  )
    return (
      <ErrorState
        error={new Error("Structured result identity or protocol differs.")}
      />
    );
  return <StructuredExecutionPanel execution={result.data} side="Selected" />;
}
export function StructuredComparisonPage() {
  const { runId } = useParams({
    from: "/workspace/engine-runs/$runId/structured-comparison",
  });
  const { before, after } = useSearch({
    from: "/workspace/engine-runs/$runId/structured-comparison",
  });
  if (!before || !after || before === after)
    return (
      <ErrorState
        error={new Error("Choose two distinct exact structured executions.")}
      />
    );
  return (
    <ReadCapability>
      <StructuredComparisonContent
        key={`${runId}:${before}:${after}`}
        runId={runId}
        before={before}
        after={after}
      />
    </ReadCapability>
  );
}
function StructuredComparisonContent({
  runId,
  before,
  after,
}: {
  runId: string;
  before: string;
  after: string;
}) {
  const records = useQuery(structuredRecordsOptions(runId));
  if (records.isPending) return <Loading />;
  if (records.error)
    return (
      <ErrorState error={records.error} retry={() => void records.refetch()} />
    );
  if (
    ![before, after].every((id) =>
      records.data.data.some(
        (row) => row.run_id === runId && row.execution_id === id,
      ),
    )
  )
    return (
      <ErrorState
        error={
          new Error(
            "Comparison identities do not belong to this exact owned run.",
          )
        }
      />
    );
  return (
    <div className="page">
      <PageHeader
        title="Factor changes and observed strategy effects"
        subtitle="Both exact results must be released before any comparison flags or values are displayed."
      />
      <a
        href={deploymentUrl(
          `/engine-runs/${runId}/structured-executions/${before}`,
        )}
      >
        Back to selected execution
      </a>
      <StructuredPairResult runId={runId} before={before} after={after} />
    </div>
  );
}
function StructuredPairResult({
  runId,
  before,
  after,
}: {
  runId: string;
  before: string;
  after: string;
}) {
  const result = useQuery(structuredComparisonOptions(runId, before, after));
  const [busy, setBusy] = useState(false),
    [error, setError] = useState<string>();
  async function release(id: string) {
    if (busy || ![before, after].includes(id)) return;
    setBusy(true);
    setError(undefined);
    try {
      await releaseStructuredExecution(runId, id);
      await result.refetch();
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "Release failed; no result was assumed.",
      );
    } finally {
      setBusy(false);
    }
  }
  if (result.isPending) return <Loading />;
  if (result.error)
    return structuredReleaseRequired(result.error) ? (
      <StructuredComparisonPanel
        display={{ state: "withheld", before, after, pending: busy, error }}
        release={(id) => void release(id)}
      />
    ) : (
      <ErrorState error={result.error} retry={() => void result.refetch()} />
    );
  const data = result.data;
  if (
    data.schema_version !== "mineagent.structured-pair-comparison.v1" ||
    data.before.run_id !== runId ||
    data.after.run_id !== runId ||
    data.before.execution_id !== before ||
    data.after.execution_id !== after
  )
    return (
      <ErrorState
        error={
          new Error(
            "Comparison response does not match the exact selected pair.",
          )
        }
      />
    );
  return (
    <StructuredComparisonPanel
      display={{ state: "released", comparison: data }}
      release={() => {}}
    />
  );
}
