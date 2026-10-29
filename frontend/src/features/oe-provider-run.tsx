import { useEffect, useRef, useState } from "react";
import { z } from "zod";
import { queryOptions, useQuery } from "@tanstack/react-query";
import { ErrorState, Loading, Panel } from "@/components/common";
import { oeRun, oeCancel } from "@/lib/oe-provider-api";
import type {
  ProviderStructuredRunView,
  WorkflowStructuredRunView,
} from "@/client/types.gen";
import { deploymentUrl } from "@/lib/deployment";
type NormalRunView = ProviderStructuredRunView | WorkflowStructuredRunView;
import { OENormalResults } from "./oe-provider-results";
import { oeBudgetFields } from "./oe-provider-parameters";
const workflowOrigin = z.strictObject({
  schema_version: z.literal("mineagent.oe-workflow-run-origin.v1"),
  id: z.uuid(),
  run_id: z.uuid(),
  owner_id: z.uuid(),
  origin: z.literal("workflow_stage"),
  workflow_id: z.uuid(),
  stage_id: z.uuid(),
  input_revision_id: z.uuid(),
  snapshot_id: z.uuid(),
  request_sha256: z.string().regex(/^[a-f0-9]{64}$/),
  preflight_sha256: z.string().regex(/^[a-f0-9]{64}$/),
  required_process_units: z.union([z.literal(4), z.literal(6)]),
});
const terminal = new Set(["succeeded", "failed", "cancelled", "interrupted"]);
export function exactOERun(value: NormalRunView, owner: string, run: string) {
  if (
    ![
      "mineagent.oe-structured-run-view.v1",
      "mineagent.oe-structured-run-view.v2",
    ].includes(value?.schema_version ?? "") ||
    value.id !== run ||
    value.engine !== "openevolve" ||
    value.purpose !== "strategy_search" ||
    value.submission?.run_id !== run ||
    (value.schema_version === "mineagent.oe-structured-run-view.v1"
      ? value.submission?.origin_user_id !== owner ||
        value.submission?.origin !== "authenticated_user_submission"
      : value.submission?.schema_version !==
          "mineagent.oe-workflow-run-origin.v1" ||
        value.submission?.origin !== "workflow_stage" ||
        value.submission?.owner_id !== owner ||
        !workflowOrigin.safeParse(value.submission).success)
  )
    throw new Error(
      "Normal OE run reply differs from the selected owner/run family.",
    );
  return value;
}
export function oeRunPollInterval(
  status?: string,
  error?: unknown,
): false | number {
  return error || (status && terminal.has(status)) ? false : 5000;
}
export const oeRunOptions = (owner: string, run: string) =>
  queryOptions({
    queryKey: ["oe-provider-run", owner, run],
    queryFn: async ({ signal }) =>
      exactOERun(await oeRun(run, signal), owner, run),
    retry: false,
    refetchInterval: (query) =>
      oeRunPollInterval(query.state.data?.status, query.state.error),
  });
export function OEProviderRunMetadata({ value }: { value: NormalRunView }) {
  return (
    <Panel title="Actual operator search status">
      <p>
        Run {value.id}: <strong>{value.status}</strong> · generation{" "}
        {value.generation}.
      </p>
      <p>
        Active attempt: {value.active_attempt_id ?? "none recorded"}. Updated{" "}
        {value.updated_at}.
      </p>
      <p>
        Exact factor version {value.factor_version_id}; dataset{" "}
        {value.dataset_version_id}; released snapshot{" "}
        {value.schema_version === "mineagent.oe-structured-run-view.v1"
          ? value.submission.normalized_request.snapshot_id
          : value.schema_version === "mineagent.oe-structured-run-view.v2"
            ? value.submission.snapshot_id
            : "not established"}
        .
      </p>
      {value.schema_version === "mineagent.oe-structured-run-view.v2" && (
        <p>
          Workflow stage origin, not a direct user submission. Stage{" "}
          {value.submission.stage_id}; frozen input revision{" "}
          {value.submission.input_revision_id}.{" "}
          <a
            href={deploymentUrl(
              `/research-workflows/${value.submission.workflow_id}`,
            )}
          >
            Open owning workflow
          </a>
          . Pause/cancel requests apply through the workflow; an admitted stage
          must actually stop and settle first.
        </p>
      )}
      <p>
        Data: {value.data_qualification ?? "not established"}. Model{" "}
        {value.model_binding.model} (
        {value.model_binding.synthetic
          ? "synthetic transport"
          : "operator transport"}
        ). Billing remains unknown.
      </p>
      <p>
        Declared cumulative process units: {value.declared_process_units}. This
        is not proof that those processes ran or stopped.
      </p>
      <table>
        <caption>Current budget accounting, not a provider invoice</caption>
        <thead>
          <tr>
            <th>Dimension</th>
            <th>Limit</th>
            <th>Used</th>
            <th>Reserved</th>
          </tr>
        </thead>
        <tbody>
          {oeBudgetFields.map((key) => (
            <tr key={key}>
              <th>{key}</th>
              <td>{value.budget_limits[key]}</td>
              <td>{value.budget_used[key]}</td>
              <td>{value.budget_reserved[key]}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p>
        Control state:{" "}
        {value.actions?.reason_code ??
          "structured_worker_control_not_registered"}
        . Pause/resume are not supported. A cancellation request is not proof of
        a stopped process.
      </p>
      <p>
        Only owned run metadata is shown here. Candidate scores, comparison
        flags, orders and fills require separate explicit result release below;
        no legacy seed fallback is used.
      </p>
    </Panel>
  );
}
export function OEProviderRunStatus({
  ownerId,
  runId,
}: {
  ownerId: string;
  runId: string;
}) {
  const value = useQuery(oeRunOptions(ownerId, runId));
  if (value.isPending) return <Loading />;
  if (value.error)
    return (
      <ErrorState error={value.error} retry={() => void value.refetch()} />
    );
  return (
    <>
      <OEProviderRunMetadata value={value.data} />
      <OECancelControl
        key={`${ownerId}:${runId}`}
        value={value.data}
        refresh={() => void value.refetch()}
      />
      <OENormalResults
        key={`${ownerId}:${runId}`}
        ownerId={ownerId}
        runId={runId}
        status={value.data.status}
      />
      <button type="button" onClick={() => void value.refetch()}>
        Refresh actual OE status
      </button>
    </>
  );
}

export function canRequestOECancel(value: NormalRunView) {
  return (
    value.schema_version === "mineagent.oe-structured-run-view.v1" &&
    value.actions?.cancel === true &&
    ["queued", "preparing", "running", "finalizing"].includes(value.status)
  );
}
export function OECancelControl({
  value,
  refresh,
}: {
  value: NormalRunView;
  refresh: () => void;
}) {
  const [busy, setBusy] = useState(false),
    [pending, setPending] = useState(false),
    [error, setError] = useState<unknown>(null);
  const active = useRef(true),
    lock = useRef(false);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  async function cancel() {
    if (!canRequestOECancel(value) || lock.current || pending) return;
    lock.current = true;
    setBusy(true);
    setError(null);
    try {
      const receipt = await oeCancel(value.id);
      if (
        receipt.schema_version !==
          "mineagent.oe-structured-control-receipt.v1" ||
        receipt.run_id !== value.id
      )
        throw new Error(
          "Cancellation receipt identity differs; refresh actual status.",
        );
      if (active.current) {
        setPending(receipt.pending);
        refresh();
      }
    } catch (failure) {
      if (active.current) setError(failure);
    } finally {
      lock.current = false;
      if (active.current) setBusy(false);
    }
  }
  return (
    <>
      {((pending && !terminal.has(value.status)) ||
        value.status === "cancel_requested") && (
        <p role="status">
          Cancellation requested; waiting for actual containment and settlement.
          No refund or positive stop is inferred.
        </p>
      )}
      {canRequestOECancel(value) && (
        <button
          type="button"
          disabled={busy || pending}
          onClick={() => void cancel()}
        >
          Request search cancellation
        </button>
      )}
      {error != null && <ErrorState error={error} />}
    </>
  );
}
