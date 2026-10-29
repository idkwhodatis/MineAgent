import {
  normalOEExecutionSelection,
  normalOEResultScopeKey,
} from "@/lib/normal-oe-execution-selection";
import { NormalOENoteRestoration } from "./normal-oe-note-restore";
import { NormalOENoteComposer } from "./normal-oe-note-composer";
import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type {
  NormalExecutionRecord,
  NormalExecutionView,
} from "@/client/types.gen";
import { ErrorState, Loading, Panel } from "@/components/common";
import {
  OECandidateLineage,
  candidateLineage,
  normalObservation,
  normalFillObservation,
  type OEObservation,
} from "./oe-candidate-lineage";
import { ObservationSteer } from "./observation-steer";
import { StructuredExecutionPanel } from "./structured-comparison";
import {
  exactNormalPair,
  exactNormalResult,
  oeComparison,
  oeRecords,
  oeRelease,
  oeResult,
  releaseRequired,
} from "@/lib/oe-results";

export function OENormalResultPanel({
  value,
  side,
  ownerId,
  onObservation,
}: {
  value: NormalExecutionView;
  side: string;
  ownerId?: string;
  onObservation?: (value: OEObservation) => void;
}) {
  const [observation, setObservation] = useState<OEObservation>();
  const [fillId, setFillId] = useState("");
  const prepare = onObservation ?? setObservation;
  return (
    <section aria-label={`${side} released normal result`}>
      {ownerId && (
        <NormalOENoteComposer
          key={`${ownerId}:${value.execution.execution_id}`}
          ownerId={ownerId}
          value={value}
        />
      )}
      <Panel title={`${side} accounting objective`}>
        <p>
          Fixed-snapshot comparison, not causal improvement or annualized gain.
        </p>
        <p>
          Eligible: {value.measurement.eligible ? "yes" : "no"}; reason:{" "}
          {value.measurement.reason}.
        </p>
        <p>
          Return: {value.measurement.total_return_pct ?? "undefined"}%; maximum
          drawdown: {value.measurement.max_drawdown_pct ?? "undefined"}%; closed
          trades: {value.measurement.closed_trade_count}.
        </p>
        <p>
          Combined score:{" "}
          {value.measurement.combined_score ?? "ineligible — no score"}{" "}
          percentage points. Targets are not observed fills.
        </p>
      </Panel>
      <p>
        Native Python source and checkpoint downloads are not provided by this
        structured result. The exact retained factor definition and program
        commitments are shown below; unavailable strategy source or parameters
        are not inferred.
      </p>
      <button type="button" onClick={() => prepare(normalObservation(value))}>
        Prepare guidance from this released {side.toLowerCase()} result
      </button>
      {value.execution.fills.length ? (
        <div>
          <label>
            {side} observed asset and session
            <select
              value={fillId}
              onChange={(event) => setFillId(event.target.value)}
            >
              <option value="">Choose an actual observed fill</option>
              {value.execution.fills.map((fill) => (
                <option key={fill.native_fill_id} value={fill.native_fill_id}>
                  {fill.listing_id} · {fill.session_date} ·{" "}
                  {fill.native_fill_id}
                </option>
              ))}
            </select>
          </label>
          <button
            disabled={
              !value.execution.fills.some(
                (fill) => fill.native_fill_id === fillId,
              )
            }
            onClick={() => prepare(normalFillObservation(value, fillId))}
          >
            Prepare guidance from selected {side.toLowerCase()} asset and
            session
          </button>
        </div>
      ) : (
        <p>
          No observed fills provide asset/session context; no asset selection is
          inferred from aggregate metrics.
        </p>
      )}
      {observation && (
        <ObservationSteer
          prepared
          key={JSON.stringify(observation)}
          observation={observation}
          onClose={() => setObservation(undefined)}
        />
      )}
      <StructuredExecutionPanel execution={value.execution} side={side} />
    </section>
  );
}
type NormalResultsProps = {
  ownerId: string;
  runId: string;
  status: string;
  onObservation?: (value: OEObservation) => void;
};
export function OENormalResults(props: NormalResultsProps) {
  return (
    <ScopedNormalResults
      key={normalOEResultScopeKey(props.ownerId, props.runId)}
      {...props}
    />
  );
}
function ScopedNormalResults({
  ownerId,
  runId,
  status,
  onObservation,
}: {
  ownerId: string;
  runId: string;
  status: string;
  onObservation?: (value: OEObservation) => void;
}) {
  const records = useQuery({
    queryKey: ["oe-normal-records", ownerId, runId, status],
    refetchInterval: ["succeeded", "failed", "cancelled"].includes(status)
      ? false
      : 2000,
    queryFn: ({ signal }) => oeRecords(runId, signal),
    retry: false,
  });
  const [selected, setSelected] = useState<string | null>(null);
  const [search, setSearch] = useState(() =>
    typeof window === "undefined" ? "" : window.location.search,
  );
  useEffect(() => {
    const changed = () => {
      setSearch(window.location.search);
      setSelected(null);
    };
    window.addEventListener("popstate", changed);
    return () => window.removeEventListener("popstate", changed);
  }, []);
  if (records.isPending) return <Loading />;
  if (records.error)
    return (
      <ErrorState error={records.error} retry={() => void records.refetch()} />
    );
  const lineage = candidateLineage(records.data);
  const route = normalOEExecutionSelection(search, records.data);
  const selectedCandidate =
    route.kind === "execution"
      ? route.candidateId
      : route.kind === "unavailable"
        ? null
        : selected;
  const choice = lineage.error
    ? undefined
    : records.data.find((row) => row.candidate_id === selectedCandidate);
  return (
    <Panel title="Recorded search candidates — metadata only">
      <p>
        Candidate identity and ancestry do not release scores, decisions, orders
        or fills. Inspect a specific execution; each side requires its own
        explicit factor-and-market exposure release.
      </p>
      {route.kind === "unavailable" && (
        <p role="alert">
          Exact requested execution is unavailable or ambiguous. No candidate
          was substituted.
        </p>
      )}
      {route.kind === "execution" && (
        <p>
          Exact execution selected from the URL; result disclosure still
          requires its existing release.
        </p>
      )}
      <NormalOENoteRestoration
        ownerId={ownerId}
        runId={runId}
        records={records.data}
        onSelected={setSelected}
      />
      {!records.data.length && <p>No candidate execution recorded yet.</p>}
      <OECandidateLineage
        records={records.data}
        selected={selectedCandidate}
        onSelect={(candidate) => {
          if (route.kind === "none") setSelected(candidate);
        }}
      />
      {choice?.execution_id && (
        <SelectedResults
          key={`${ownerId}:${runId}:${choice.candidate_id}:${choice.execution_id}`}
          ownerId={ownerId}
          runId={runId}
          after={choice}
          onObservation={onObservation}
          before={records.data.find(
            (row) =>
              row.candidate_id === choice.parent_candidate_id &&
              row.execution_id !== null,
          )}
        />
      )}
    </Panel>
  );
}
function SelectedResults({
  ownerId,
  runId,
  after,
  before,
  onObservation,
}: {
  ownerId: string;
  runId: string;
  after: NormalExecutionRecord;
  before?: NormalExecutionRecord;
  onObservation?: (value: OEObservation) => void;
}) {
  const [ready, setReady] = useState<Set<string>>(new Set());
  const [compare, setCompare] = useState(false);
  const pair = useQuery({
    queryKey: [
      "oe-normal-pair",
      ownerId,
      runId,
      before?.execution_id,
      after.execution_id,
    ],
    queryFn: async ({ signal }) =>
      exactNormalPair(
        await oeComparison(
          runId,
          before!.execution_id!,
          after.execution_id!,
          signal,
        ),
        runId,
        before!,
        after,
      ),
    enabled:
      compare &&
      !!before &&
      ready.has(before.execution_id!) &&
      ready.has(after.execution_id!),
    retry: false,
  });
  const released = useCallback((id: string) => {
    setReady((old) => (old.has(id) ? old : new Set([...old, id])));
  }, []);
  return (
    <>
      {before && (
        <ReleasedExecution
          ownerId={ownerId}
          runId={runId}
          record={before}
          side="Parent"
          onRead={released}
          onObservation={onObservation}
        />
      )}
      <ReleasedExecution
        ownerId={ownerId}
        runId={runId}
        record={after}
        side={before ? "Child" : "Selected"}
        onRead={released}
        onObservation={onObservation}
      />
      {before && (
        <>
          <button
            type="button"
            disabled={
              !ready.has(before.execution_id!) ||
              !ready.has(after.execution_id!)
            }
            onClick={() => setCompare(true)}
          >
            Compare released parent and child
          </button>
          {compare &&
            (pair.isPending ? (
              <Loading />
            ) : pair.error ? (
              <ErrorState
                error={pair.error}
                retry={() => void pair.refetch()}
              />
            ) : (
              <Panel title="Actual direct parent–child comparison">
                <p>
                  Score delta:{" "}
                  {pair.data.score_delta_percentage_points ??
                    "not defined for this pair"}{" "}
                  percentage points.
                </p>
                <p>
                  Same fixed snapshot; not causal gain, strategy superiority, or
                  a provider invoice.
                </p>
              </Panel>
            ))}
        </>
      )}
    </>
  );
}
function ReleasedExecution({
  ownerId,
  runId,
  record,
  side,
  onRead,
  onObservation,
}: {
  ownerId: string;
  runId: string;
  record: NormalExecutionRecord;
  side: string;
  onRead: (id: string) => void;
  onObservation?: (value: OEObservation) => void;
}) {
  const result = useQuery({
    queryKey: ["oe-normal-result", ownerId, runId, record.execution_id],
    queryFn: async ({ signal }) =>
      exactNormalResult(
        await oeResult(runId, record.execution_id!, signal),
        runId,
        record,
      ),
    retry: false,
  });
  const [ack, setAck] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState<unknown>(null);
  const lock = useRef(false),
    active = useRef(true);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  useEffect(() => {
    if (result.data && !result.error)
      onRead(result.data.execution.execution_id);
  }, [result.data, result.error, onRead]);
  async function release() {
    if (!ack || lock.current) return;
    lock.current = true;
    setBusy(true);
    setError(null);
    try {
      await oeRelease(runId, record.execution_id!);
      if (active.current) await result.refetch();
    } catch (failure) {
      if (active.current) setError(failure);
    } finally {
      lock.current = false;
      if (active.current) setBusy(false);
    }
  }
  if (result.isPending) return <Loading />;
  if (result.error)
    return releaseRequired(result.error) ? (
      <Panel title={`${side} explicit result release required`}>
        <p>
          Release records exposure to the full retained factor and market
          datasets, including historical and forward-label extent. Labels are
          not trading inputs. No values or comparison are shown before a
          committed release and verified read.
        </p>
        <label>
          <input
            type="checkbox"
            checked={ack}
            disabled={busy}
            onChange={(event) => setAck(event.target.checked)}
          />
          I explicitly authorize {side.toLowerCase()} factor and market result
          exposure.
        </label>
        <button
          type="button"
          disabled={busy || !ack}
          onClick={() => void release()}
        >
          Release {side.toLowerCase()} execution {record.execution_id}
        </button>
        {error != null && <ErrorState error={error} />}
      </Panel>
    ) : (
      <ErrorState error={result.error} retry={() => void result.refetch()} />
    );
  return (
    <OENormalResultPanel
      value={result.data}
      ownerId={ownerId}
      side={side}
      onObservation={onObservation}
    />
  );
}
