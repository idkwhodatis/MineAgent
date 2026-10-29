import { deploymentUrl } from "@/lib/deployment";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { ErrorState, Loading, Panel } from "@/components/common";
import type {
  ProviderStructuredPreflight,
  ProviderStructuredSearchRequest,
} from "@/client/types.gen";
import { OEProviderSelection } from "./oe-provider-selection";
import { OEProviderParameters } from "./oe-provider-parameters";
import { OEProviderPreflightResult } from "./oe-provider-preflight";
import { OEProviderRunStatus } from "./oe-provider-run";
import {
  checkOESearch,
  clearOEReceipt,
  clearOESubmission,
  loadOEReceipt,
  loadOESubmission,
  oeDefinitelyNotAccepted,
  oeModelsOptions,
  oeSnapshotsOptions,
  prepareOESubmission,
  sameOERequest,
  saveOEReceipt,
  saveOESubmission,
  submitSavedOE,
  type OEPendingSubmission,
} from "@/lib/oe-provider-entry";

export function emptyOEDeclaration(): ProviderStructuredSearchRequest {
  return {
    schema_version: "mineagent.oe-structured-provider-submission.v1",
    purpose: "strategy_search",
    engine: "openevolve",
    profile: "fixed_factor_operator_closed_program_v1",
    snapshot_id: "",
    model_profile_id: "",
    model_binding_sha256: "",
    seed_parameters: {
      threshold: Number.NaN,
      top_k: Number.NaN,
      maximum_weight: Number.NaN,
      maximum_gross_weight: Number.NaN,
    },
    objective: {
      schema_version: "mineagent.oe-structured-objective.v1",
      window: { start: "", end: "" },
      initial_cash: Number.NaN,
      metric: "total_return_pct_minus_max_drawdown_pct",
      unit: "percentage_points",
      direction: "maximize",
      source: "validated_closed_engine_accounting_summary",
      precision: "unrounded_native_accounting",
      missing_policy: "ineligible_null_score",
      minimum_closed_trades: null,
    },
    budget: {
      calls: 0,
      tokens: 0,
      mutation_slots: 0,
      evaluator_executions: 0,
      wall_milliseconds: 0,
      storage_bytes: 0,
      process_slots: 0,
      cost_microunits: 0,
    },
    maximum_mutation_slots: 1,
  };
}
/** Mount only after coherent normal027 routes exist; never use fixture availability. */
export function OEProviderEntry(props: {
  ownerId: string;
  routesAvailable?: boolean;
}) {
  return <OEProviderEntryForOwner key={props.ownerId} {...props} />;
}
function OEProviderEntryForOwner({
  ownerId,
  routesAvailable = false,
}: {
  ownerId: string;
  routesAvailable?: boolean;
}) {
  const [offset, setOffset] = useState(0);
  const models = useQuery({
    ...oeModelsOptions(ownerId),
    enabled: routesAvailable,
  });
  const snapshots = useQuery({
    ...oeSnapshotsOptions(ownerId, offset),
    enabled: routesAvailable,
  });
  const [draft, setDraft] = useState(emptyOEDeclaration);
  const [checked, setChecked] = useState<ProviderStructuredPreflight | null>(
    null,
  );
  const [saved, setSaved] = useState(() => loadOESubmission(ownerId));
  const [accepted, setAccepted] = useState(() => loadOEReceipt(ownerId));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const lock = useRef(false),
    generation = useRef(0),
    active = useRef(true);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  const model = models.data?.find(
    (value) => value.binding.profile_id === draft.model_profile_id,
  );
  const selectedSnapshot = snapshots.data?.items.find(
    (value) => value.snapshot_id === draft.snapshot_id,
  );
  const editable = saved.state === "empty" && !busy && accepted === null;
  const fresh =
    checked !== null &&
    sameOERequest(checked.normalized_request, draft) &&
    model?.operator_available === true &&
    model.binding_sha256 === draft.model_binding_sha256 &&
    selectedSnapshot !== undefined;
  function change(value: ProviderStructuredSearchRequest) {
    generation.current += 1;
    setDraft(value);
    setChecked(null);
  }
  async function check(event: FormEvent) {
    event.preventDefault();
    if (
      !routesAvailable ||
      !editable ||
      lock.current ||
      !model ||
      !selectedSnapshot ||
      !model.operator_available ||
      model.binding_sha256 !== draft.model_binding_sha256
    )
      return;
    lock.current = true;
    setBusy(true);
    setError(null);
    const revision = generation.current;
    try {
      const result = await checkOESearch(draft);
      if (!sameOERequest(result.normalized_request, draft))
        throw new Error(
          "Server normalized a different declaration; review the exact inputs before proceeding.",
        );
      if (active.current && revision === generation.current) setChecked(result);
    } catch (failure) {
      if (active.current) setError(failure);
    } finally {
      lock.current = false;
      if (active.current) setBusy(false);
    }
  }
  async function send(envelope: OEPendingSubmission) {
    if (!routesAvailable || lock.current) return;
    lock.current = true;
    setBusy(true);
    setError(null);
    try {
      const response = await submitSavedOE(envelope);
      if (!saveOEReceipt(ownerId, response))
        throw new Error(
          "Receipt received but could not be saved. Keep the original retry identity.",
        );
      if (!clearOESubmission(envelope))
        throw new Error(
          "Receipt received but pending identity changed. Do not start a replacement automatically.",
        );
      if (active.current) {
        setAccepted(response);
        setSaved({ state: "empty" });
        setChecked(null);
      }
    } catch (failure) {
      if (
        oeDefinitelyNotAccepted(failure) &&
        clearOESubmission(envelope) &&
        active.current
      ) {
        setSaved({ state: "empty" });
        setChecked(null);
      }
      if (active.current) setError(failure);
    } finally {
      lock.current = false;
      if (active.current) setBusy(false);
    }
  }
  function submit() {
    if (
      lock.current ||
      !checked ||
      !fresh ||
      !checked.can_submit ||
      saved.state !== "empty" ||
      busy ||
      !routesAvailable
    )
      return;
    try {
      const envelope = prepareOESubmission(ownerId, checked);
      if (!saveOESubmission(envelope))
        throw new Error(
          "Could not preserve the original submission. Nothing was sent.",
        );
      setSaved({ state: "pending", envelope });
      void send(envelope);
    } catch (failure) {
      setError(failure);
    }
  }
  if (!routesAvailable)
    return (
      <Panel title="Normal OpenEvolve search unavailable">
        <p>
          The registered operator-backed route and schema are not available. The
          maintained fixture is not a substitute.
        </p>
      </Panel>
    );
  return (
    <div>
      <h2>Operator-backed fixed-factor strategy search</h2>
      <p>
        No arbitrary Python, endpoint, image or credential is supplied here. The
        server must freshly admit the exact released factor, model and declared
        limits.
      </p>
      {error !== null && <ErrorState error={error} />}
      {saved.state === "invalid" && (
        <p role="alert">
          Saved submission is malformed or unreadable. No replacement will be
          sent automatically.
        </p>
      )}
      {saved.state === "pending" && (
        <Panel title="Unresolved original submission">
          <p>
            The original key and exact body are retained across reloads. Current
            model availability does not replace an accepted request. Retry the
            original identity to resolve it.
          </p>
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              saved.state === "pending" && void send(saved.envelope)
            }
          >
            Retry exact original OE submission
          </button>
        </Panel>
      )}
      {accepted && (
        <Panel title="Recorded submission receipt">
          <p>
            Run {accepted.run_id}; receipt {accepted.id}. This records
            acceptance, not current run status or successful execution.
          </p>
          <a
            className="text-link"
            href={deploymentUrl(
              `/engine-runs/structured-search/runs/${encodeURIComponent(accepted.run_id)}`,
            )}
          >
            Open owned search status
          </a>
          <p className="break-all">
            Request SHA-256: {accepted.request_sha256}
          </p>
          <p>
            Current status is read from the dedicated normal operator route,
            never a legacy seed fallback.
          </p>
          <button
            type="button"
            disabled={busy || saved.state !== "empty"}
            onClick={() => {
              if (clearOEReceipt(ownerId, accepted.id)) {
                setAccepted(null);
                change(emptyOEDeclaration());
              } else
                setError(
                  new Error(
                    "Saved receipt changed; reload before starting another request.",
                  ),
                );
            }}
          >
            Start another distinct search
          </button>
        </Panel>
      )}
      {accepted && (
        <OEProviderRunStatus ownerId={ownerId} runId={accepted.run_id} />
      )}
      {models.isPending || snapshots.isPending ? (
        <Loading />
      ) : models.error || snapshots.error ? (
        <ErrorState
          error={models.error ?? snapshots.error}
          retry={() => {
            void models.refetch();
            void snapshots.refetch();
          }}
        />
      ) : (
        <form onSubmit={(event) => void check(event)}>
          <OEProviderSelection
            snapshots={snapshots.data.items}
            models={models.data}
            snapshotId={draft.snapshot_id}
            modelProfileId={draft.model_profile_id}
            disabled={!editable}
            onSnapshot={(id) => change({ ...draft, snapshot_id: id })}
            onModel={(id) =>
              change({
                ...draft,
                model_profile_id: id,
                model_binding_sha256:
                  models.data.find((value) => value.binding.profile_id === id)
                    ?.binding_sha256 ?? "",
              })
            }
          />
          <button
            type="button"
            disabled={!editable || offset === 0}
            onClick={() => setOffset(Math.max(0, offset - 20))}
          >
            Previous released snapshots
          </button>
          <button
            type="button"
            disabled={!editable || !snapshots.data.has_more}
            onClick={() => setOffset(offset + 20)}
          >
            Next released snapshots
          </button>
          {model && model.binding_sha256 !== draft.model_binding_sha256 && (
            <p role="alert">
              The operator binding changed.{" "}
              <button
                type="button"
                disabled={!editable}
                onClick={() =>
                  change({
                    ...draft,
                    model_binding_sha256: model.binding_sha256,
                  })
                }
              >
                Use current binding and recheck
              </button>
            </p>
          )}
          <OEProviderParameters
            request={draft}
            disabled={!editable}
            onChange={change}
          />
          <button
            type="submit"
            disabled={
              !editable ||
              !selectedSnapshot ||
              !model?.operator_available ||
              model.binding_sha256 !== draft.model_binding_sha256
            }
          >
            Check normal OE eligibility
          </button>
        </form>
      )}
      {checked && (
        <OEProviderPreflightResult
          result={checked}
          stale={!fresh}
          locked={!editable}
          onApplyBudget={(budget) => change({ ...draft, budget })}
        />
      )}
      <button
        type="button"
        disabled={!editable || !fresh || !checked?.can_submit}
        onClick={submit}
      >
        Submit exact checked OE request
      </button>
    </div>
  );
}
