import {
  generationCanCreate,
  preflightMatchesSelection,
} from "@/lib/rd-provider-inputs";
import { useState, useEffect, useRef, type FormEvent } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { ErrorState, Loading, PageHeader, Panel } from "@/components/common";
import { SteerVersionSelection } from "./rd-steers";
import { steerCapabilityOptions, steerListOptions } from "@/lib/rd-steers";
import type { EnginePreflight, EngineRequest } from "@/lib/engine-runs-api";
import type { RdSubmissionRequest } from "@/client/types.gen";
import {
  engineCapabilitiesOptions,
  engineDatasetsOptions,
  preflightEngineRun,
} from "@/lib/engine-runs-api";

import { currentUserOptions } from "@/lib/api";
import {
  clearEngineSubmission,
  definitelyNotAccepted,
  loadEngineSubmission,
  prepareEngineSubmission,
  saveEngineSubmission,
  type EngineSubmissionEnvelope,
} from "@/lib/engine-submission-draft";
import { submitSavedEngineRequest } from "@/lib/engine-submission-api";

export function EnginePreflightPage() {
  const search = useSearch({ from: "/workspace/engine-runs/preflight" }) as {
    parent_factor_version_id?: string;
  };
  const owner = useQuery(currentUserOptions);
  if (owner.isPending) return <Loading />;
  if (owner.error)
    return (
      <ErrorState error={owner.error} retry={() => void owner.refetch()} />
    );
  return (
    <EngineEntryForm
      key={`${owner.data.id}:${search.parent_factor_version_id ?? ""}`}
      ownerId={owner.data.id}
      initialParent={search.parent_factor_version_id ?? ""}
    />
  );
}

function EngineEntryForm({
  ownerId,
  initialParent,
}: {
  ownerId: string;
  initialParent: string;
}) {
  const navigate = useNavigate();
  const [saved, setSaved] = useState(() => loadEngineSubmission(ownerId));
  const [submitting, setSubmitting] = useState(false);
  const [submissionError, setSubmissionError] = useState<unknown>(null);
  const [acceptedRun, setAcceptedRun] = useState<{
    runId: string;
    status: string;
  } | null>(null);
  const active = useRef(true),
    submissionLock = useRef(false);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  const [offset, setOffset] = useState(0);
  const capabilities = useQuery(engineCapabilitiesOptions);
  const datasets = useQuery(engineDatasetsOptions(offset));
  const [result, setResult] = useState<EnginePreflight | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [pending, setPending] = useState(false);
  const profile = capabilities.data?.engines.find(
    (v) => v.engine === "rdagent" && v.purpose === "factor_research",
  );
  const [mode, setMode] = useState<"fixture" | "configured">(
    initialParent ? "configured" : "fixture",
  );
  const [modelId, setModelId] = useState("");
  const [parentVersionId, setParentVersionId] = useState(initialParent);
  const [selectedSteers, setSelectedSteers] = useState<string[]>([]);
  const steerCapability = useQuery({
    ...steerCapabilityOptions,
    enabled: mode === "configured",
  });
  const steers = useQuery({
    ...steerListOptions,
    enabled: mode === "configured" && steerCapability.data?.can_manage === true,
  });
  const selectedModel = profile?.model_profiles?.find(
    (option) => option.id === modelId,
  );
  const budget =
    mode === "fixture"
      ? profile?.recommended_budget
      : selectedModel?.recommended_budget;
  const canCreate = generationCanCreate(mode, profile, selectedModel);
  const matchesSelection = preflightMatchesSelection(
    result,
    mode,
    modelId,
    parentVersionId,
    selectedSteers,
  );
  function invalidatePreflight() {
    setResult(null);
    setError(null);
  }
  async function send(envelope: EngineSubmissionEnvelope) {
    if (submissionLock.current) return;
    submissionLock.current = true;
    setSubmitting(true);
    setSubmissionError(null);
    try {
      if (!saveEngineSubmission(envelope)) {
        setSaved(loadEngineSubmission(ownerId));
        throw new Error(
          "The exact retry request could not be saved. No request was sent.",
        );
      }
      setSaved({ state: "pending", envelope });
      const receipt = await submitSavedEngineRequest(envelope);
      if (!active.current) return;
      setAcceptedRun({ runId: receipt.run_id, status: receipt.status });
      clearEngineSubmission(envelope);
      setSaved(loadEngineSubmission(ownerId));
      await navigate({
        to: "/engine-runs/$runId",
        params: { runId: receipt.run_id },
        search: { offset: 0 },
      });
    } catch (failure) {
      if (!active.current) return;
      if (definitelyNotAccepted(failure, envelope)) {
        clearEngineSubmission(envelope);
        setSaved(loadEngineSubmission(ownerId));
        setResult(null);
      }
      setSubmissionError(failure);
    } finally {
      submissionLock.current = false;
      if (active.current) setSubmitting(false);
    }
  }
  function create() {
    if (
      !canCreate ||
      !matchesSelection ||
      !result?.can_submit ||
      saved.state !== "empty" ||
      acceptedRun
    )
      return;
    try {
      void send(prepareEngineSubmission(ownerId, result));
    } catch (failure) {
      setSubmissionError(failure);
    }
  }
  async function check(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      !profile?.can_preflight ||
      !budget ||
      pending ||
      saved.state !== "empty" ||
      acceptedRun
    )
      return;
    const data = new FormData(event.currentTarget);
    const text = (key: string) => String(data.get(key) ?? "");
    const number = (key: string) => Number(text(key));
    if (data.get("acknowledged") !== "on") return;
    const window = { start: text("start"), end: text("end") };
    const declaration: RdSubmissionRequest = {
      schema_version: "mineagent.rdagent-submission.v1",
      purpose: "factor_research",
      engine: "rdagent",
      dataset_version_id: text("dataset"),
      window,
      input_fields: data.getAll(
        "input_fields",
      ) as RdSubmissionRequest["input_fields"],
      lookback_sessions: number("lookback"),
      lag_sessions: number("lag"),
      max_iterations: number("iterations"),
      task: {
        name: text("name"),
        description: text("description"),
        formulation: text("formulation"),
        factor_kind: text("kind") as "time_series" | "cross_sectional",
        output_unit: text("unit"),
        interpretation: text("interpretation") as
          "higher_preferred" | "lower_preferred" | "unsigned",
      },
      objective: {
        window,
        label_horizon_sessions: number("horizon"),
        metric: text("metric") as "ic" | "rank_ic",
        direction: text("direction") as "at_least" | "at_most",
        threshold: number("threshold"),
        minimum_defined_days: number("days"),
        minimum_pairs: number("pairs"),
      },
      budget,
      source_policy: { acknowledged_unverified: true },
    };
    const request: EngineRequest =
      mode === "fixture"
        ? declaration
        : parentVersionId
          ? {
              ...declaration,
              schema_version: "mineagent.rdagent-provider-seeded-submission.v1",
              model_profile_id: modelId,
              selected_steer_version_ids: selectedSteers,
              parent_factor_version_id: parentVersionId,
            }
          : {
              ...declaration,
              schema_version: "mineagent.rdagent-provider-submission.v1",
              model_profile_id: modelId,
              selected_steer_version_ids: selectedSteers,
            };
    if (
      mode === "configured" &&
      (!selectedModel ||
        (selectedSteers.length > 0 &&
          (steerCapability.data?.can_manage !== true ||
            !steers.data ||
            selectedSteers.some(
              (id) =>
                !steers.data.data.some(
                  (row) => !row.disabled && row.current.version_id === id,
                ),
            ))))
    ) {
      setError(
        new Error(
          "Choose a server model and explicitly refresh changed/unavailable steer selections before preflight.",
        ),
      );
      return;
    }
    setPending(true);
    setResult(null);
    setError(null);
    try {
      setResult(await preflightEngineRun(request));
    } catch (e) {
      setError(e);
    } finally {
      setPending(false);
    }
  }
  return (
    <div className="page">
      <Link to="/engine-runs" search={{ offset: 0 }} className="text-link">
        Back to engine history
      </Link>
      <PageHeader
        title="Check RD-Agent research eligibility"
        subtitle="Preflight validates retained data without execution. Creating a run requires an explicit accepted server profile and a separate submission."
      />
      {acceptedRun && (
        <Panel title="Server accepted this request">
          <p>
            Status at receipt: {acceptedRun.status}. No duplicate submission
            will be created from this form.
          </p>
          <Link
            to="/engine-runs/$runId"
            params={{ runId: acceptedRun.runId }}
            search={{ offset: 0 }}
          >
            Open accepted research run
          </Link>
        </Panel>
      )}
      {saved.state === "invalid" && (
        <Panel title="Saved request cannot be verified">
          <p>
            Tab storage is unavailable or contains an invalid unresolved
            request. No new request will be sent. Check your existing research
            history before resolving this storage problem.
          </p>
        </Panel>
      )}
      {saved.state === "pending" && (
        <Panel title="Unresolved saved submission">
          <p>
            Editing is locked until this exact request is reconciled. A timeout
            or reload is not evidence that no run was created. Retry uses the
            same owner, body and key, even if the runtime is now disabled.
          </p>
          <p className="break-all">
            Request: {saved.envelope.request_sha256}
            <br />
            Retry key: {saved.envelope.key}
          </p>
          <button
            type="button"
            disabled={submitting}
            onClick={() => void send(saved.envelope)}
          >
            {submitting
              ? "Checking saved submission…"
              : "Retry exact saved submission"}
          </button>
        </Panel>
      )}
      {submissionError != null && <ErrorState error={submissionError} />}
      {capabilities.isPending ? (
        <Loading />
      ) : capabilities.error ? (
        <ErrorState
          error={capabilities.error}
          retry={() => void capabilities.refetch()}
        />
      ) : !profile?.can_preflight ? (
        <Panel title="Preflight unavailable">
          <p>No accepted preflight capability is available.</p>
        </Panel>
      ) : (
        <>
          <Panel title="Research generation mode">
            <fieldset
              disabled={
                pending ||
                submitting ||
                saved.state !== "empty" ||
                Boolean(acceptedRun)
              }
            >
              <label htmlFor="engine-generation-mode">Generation mode</label>
              <select
                id="engine-generation-mode"
                value={mode}
                onChange={(event) => {
                  setMode(event.target.value as "fixture" | "configured");
                  invalidatePreflight();
                }}
              >
                <option value="fixture">
                  Maintained fixed close-return fixture
                </option>
                <option value="configured">
                  Configured model (server-authorized)
                </option>
              </select>
              {mode === "configured" && (
                <>
                  <p>
                    Configured models may generate proposals; they are not
                    automatically approved or verified. No provider endpoint or
                    credential is supplied by this form.
                  </p>
                  <label htmlFor="engine-model-profile">
                    Configured model profile
                  </label>
                  <select
                    id="engine-model-profile"
                    required
                    value={modelId}
                    onChange={(event) => {
                      setModelId(event.target.value);
                      invalidatePreflight();
                    }}
                  >
                    <option value="">
                      Select an explicitly configured model
                    </option>
                    {(profile.model_profiles ?? []).map((option) => (
                      <option key={option.id} value={option.id}>
                        {option.id} · {option.chat_model} /{" "}
                        {option.embedding_model}
                        {option.synthetic ? " · synthetic transport" : ""}
                      </option>
                    ))}
                  </select>
                  {!profile.model_profiles?.length && (
                    <p>
                      No authorized model profiles are available.{" "}
                      {profile.configured_model_reason_code}
                    </p>
                  )}
                  {selectedModel && (
                    <>
                      <p>
                        Pricing: {selectedModel.pricing_status}. Billing status:{" "}
                        {selectedModel.billing_status}. Operator-declared prices
                        are not a verified provider bill; unknown is not zero.
                      </p>
                      <p>Availability: {selectedModel.reason_code}</p>
                    </>
                  )}
                  <label>
                    Exact parent factor version (optional; leave empty for a new
                    root)
                    <input
                      value={parentVersionId}
                      onChange={(event) => {
                        setParentVersionId(event.target.value);
                        invalidatePreflight();
                      }}
                      placeholder="Owned factor-version UUID"
                    />
                  </label>
                  <p>
                    A parent must be owned and retained. Preflight resolves its
                    exact code and definition; an ID alone is not an execution
                    grant.
                  </p>
                  {steerCapability.isPending ? (
                    <Loading />
                  ) : steerCapability.error ? (
                    <ErrorState error={steerCapability.error} />
                  ) : !steerCapability.data?.can_manage ? (
                    <p>
                      Steer selection unavailable:{" "}
                      {steerCapability.data?.reason_code}. No saved input is
                      selected automatically.
                    </p>
                  ) : steers.isPending ? (
                    <Loading />
                  ) : steers.error ? (
                    <ErrorState error={steers.error} />
                  ) : (
                    <SteerVersionSelection
                      rows={steers.data.data}
                      selected={selectedSteers}
                      onChange={(ids) => {
                        setSelectedSteers(ids);
                        invalidatePreflight();
                      }}
                    />
                  )}
                </>
              )}
            </fieldset>
          </Panel>
          <Panel title="Server-controlled profile">
            <p>
              Model:{" "}
              {mode === "fixture"
                ? profile.model_profile
                : (selectedModel?.id ?? "none selected")}
              . Maximum output sessions: {profile.max_output_sessions};
              iterations: {profile.max_iterations}. Unsupported windows are
              rejected before queueing.
            </p>
            {mode === "fixture" &&
              profile.model_profile === "fixture.rdagent.v1" && (
                <p>
                  This fixture profile supports only time-series close returns
                  over the declared lookback L: close[t]/close[t-L]-1, with
                  close input and return output unit. Other tasks fail
                  preflight. It is not a general task-generation model.
                </p>
              )}
            <p>
              Declared daily availability and fixed membership are user
              declarations, not verified point-in-time data. Research selection
              is not out-of-sample performance.
            </p>
            <details>
              <summary>Server-recommended bounded budget</summary>
              <pre>{JSON.stringify(budget ?? null, null, 2)}</pre>
            </details>
          </Panel>
          {datasets.isPending ? (
            <Loading />
          ) : datasets.error ? (
            <ErrorState
              error={datasets.error}
              retry={() => void datasets.refetch()}
            />
          ) : (
            <Panel title="Retained dataset and objective">
              <form
                onSubmit={check}
                onChange={() => {
                  setResult(null);
                  setError(null);
                }}
              >
                <fieldset
                  disabled={
                    pending ||
                    submitting ||
                    saved.state !== "empty" ||
                    Boolean(acceptedRun)
                  }
                >
                  <label htmlFor="engine-retained-dataset">
                    Retained dataset
                  </label>
                  <select
                    id="engine-retained-dataset"
                    name="dataset"
                    required
                    defaultValue=""
                    key={offset}
                  >
                    <option value="" disabled>
                      Select an owned dataset
                    </option>
                    {datasets.data.data.map((d) => (
                      <option
                        key={d.id}
                        value={d.id}
                        disabled={d.eligibility === "unsupported"}
                      >
                        {d.name} · {d.market} · {d.start_date}–{d.end_date}
                        {d.is_synthetic ? " · synthetic" : ""}
                        {d.reason
                          ? ` · ${d.reason.message}`
                          : " · eligibility not yet checked"}
                      </option>
                    ))}
                  </select>
                  {!datasets.data.data.length && (
                    <p>
                      No retained datasets on this page. Import a dataset before
                      checking eligibility.
                    </p>
                  )}
                  <button
                    type="button"
                    disabled={offset === 0}
                    onClick={() => {
                      setOffset(Math.max(0, offset - 25));
                      setResult(null);
                    }}
                  >
                    Previous datasets
                  </button>
                  <button
                    type="button"
                    disabled={offset + 25 >= datasets.data.count}
                    onClick={() => {
                      setOffset(offset + 25);
                      setResult(null);
                    }}
                  >
                    Next datasets
                  </button>
                  <label>
                    Output window start (inclusive)
                    <input name="start" type="date" required />
                  </label>
                  <label>
                    Output window end (exclusive)
                    <input name="end" type="date" required />
                  </label>
                  <fieldset>
                    <legend>Input fields</legend>
                    {["open", "high", "low", "close", "volume"].map((field) => (
                      <label key={field}>
                        <input
                          type="checkbox"
                          name="input_fields"
                          value={field}
                          defaultChecked={field === "close"}
                        />
                        {field}
                      </label>
                    ))}
                  </fieldset>
                  <label>
                    Prior lookback sessions
                    <input
                      name="lookback"
                      type="number"
                      min="1"
                      max="63"
                      defaultValue="1"
                      required
                    />
                  </label>
                  <label>
                    Lag sessions
                    <input
                      name="lag"
                      type="number"
                      min="0"
                      max="8"
                      defaultValue="0"
                      required
                    />
                  </label>
                  <label>
                    Iterations
                    <input
                      name="iterations"
                      type="number"
                      min="1"
                      max="8"
                      defaultValue={profile.max_iterations ?? 1}
                      required
                    />
                  </label>
                  <label>
                    Task identifier
                    <input name="name" required maxLength={128} />
                  </label>
                  <label>
                    Task description
                    <textarea name="description" required />
                  </label>
                  <label>
                    Factor formulation
                    <textarea name="formulation" required />
                  </label>
                  <label htmlFor="engine-factor-kind">Factor kind</label>
                  <select
                    id="engine-factor-kind"
                    name="kind"
                    defaultValue="time_series"
                  >
                    <option value="cross_sectional">Cross-sectional</option>
                    <option value="time_series">Time series</option>
                  </select>
                  <label>
                    Output unit identifier
                    <input name="unit" defaultValue="return" required />
                  </label>
                  <label htmlFor="engine-interpretation">Interpretation</label>
                  <select id="engine-interpretation" name="interpretation">
                    <option value="higher_preferred">Higher preferred</option>
                    <option value="lower_preferred">Lower preferred</option>
                    <option value="unsigned">Unsigned</option>
                  </select>
                  <label>
                    Label horizon sessions
                    <input
                      name="horizon"
                      type="number"
                      min="1"
                      max="4096"
                      defaultValue="1"
                      required
                    />
                  </label>
                  <label htmlFor="engine-objective-metric">
                    Objective metric
                  </label>
                  <select id="engine-objective-metric" name="metric">
                    <option value="ic">IC</option>
                    <option value="rank_ic">Rank IC</option>
                  </select>
                  <label htmlFor="engine-threshold-direction">
                    Threshold direction
                  </label>
                  <select id="engine-threshold-direction" name="direction">
                    <option value="at_least">At least</option>
                    <option value="at_most">At most</option>
                  </select>
                  <label>
                    Threshold
                    <input
                      name="threshold"
                      type="number"
                      min="-1"
                      max="1"
                      step="any"
                      required
                    />
                  </label>
                  <label>
                    Minimum defined days
                    <input
                      name="days"
                      type="number"
                      min="1"
                      max="4096"
                      defaultValue="1"
                      required
                    />
                  </label>
                  <label>
                    Minimum pairs
                    <input
                      name="pairs"
                      type="number"
                      min="2"
                      max="10000"
                      defaultValue="2"
                      required
                    />
                  </label>
                  <label>
                    <input name="acknowledged" type="checkbox" required />I
                    declare daily session-close availability, fixed membership
                    from source start, and unadjusted prices. These declarations
                    have not been independently verified.
                  </label>
                  <button type="submit" disabled={!budget}>
                    {pending ? "Checking retained data…" : "Check eligibility"}
                  </button>
                </fieldset>
              </form>
            </Panel>
          )}
          {error != null && <ErrorState error={error} />}
          {result && (
            <Panel title="Authoritative preflight result">
              <p>
                {result.data_eligible
                  ? "Retained data eligible"
                  : "Retained data not eligible"}
                .{" "}
                {result.can_submit
                  ? "Server reports submission eligibility; no run has been created by preflight."
                  : "Submission unavailable."}
              </p>
              <p>
                Output cutoffs: {result.cutoff_count}; required process units:{" "}
                {result.required_process_units}.
              </p>
              <ul>
                {result.reasons.map((reason, i) => (
                  <li key={`${reason.code}-${i}`}>
                    <code>{reason.code}</code>: {reason.message}
                  </li>
                ))}
              </ul>
              <p className="break-all">
                Request: {result.request_sha256}
                <br />
                Preflight: {result.preflight_sha256}
              </p>
              {"steer_snapshot" in result && (
                <Panel title="Exact user-provided steer snapshot (pinned)">
                  <p>
                    Snapshot commitment: {result.steer_snapshot_sha256}. This is
                    the ordered preflight input, not proof of model compliance.
                    A new edit requires new preflight; accepted runs retain
                    their original snapshot.
                  </p>
                  <ol>
                    {(result.steer_snapshot.versions ?? []).map((version) => (
                      <li key={version.version_id}>
                        <strong>
                          {version.kind} · v{version.ordinal} ·{" "}
                          {version.version_id}
                        </strong>
                        <p className="whitespace-pre-wrap">{version.text}</p>
                        <p className="break-all">
                          Text SHA-256: {version.text_sha256}
                        </p>
                      </li>
                    ))}
                  </ol>
                  {!(result.steer_snapshot.versions ?? []).length && (
                    <p>No steer versions selected.</p>
                  )}
                  {result.parent && (
                    <p>
                      Exact parent factor {result.parent.factor_id}, version{" "}
                      {result.parent.factor_version_id}, ordinal{" "}
                      {result.parent.ordinal}; code SHA-256{" "}
                      {result.parent.code_sha256}. Parent binding{" "}
                      {result.parent_binding_sha256}.
                    </p>
                  )}
                  {result.model_binding && (
                    <p>
                      Frozen model commitment: {result.model_binding_sha256}.
                      Model: {result.model_binding.profile_id} ·{" "}
                      {result.model_binding.chat_model} /{" "}
                      {result.model_binding.embedding_model}; pricing{" "}
                      {result.model_binding.pricing.status}. Actual billing
                      remains unknown.
                    </p>
                  )}
                </Panel>
              )}
              <p>
                No run or analysis plan was created. Editing the form
                invalidates this displayed preflight. Reloading requires another
                read-only check.
              </p>
            </Panel>
          )}
          <button
            type="button"
            disabled={
              !canCreate ||
              !matchesSelection ||
              !result?.can_submit ||
              submitting ||
              pending ||
              saved.state !== "empty" ||
              Boolean(acceptedRun)
            }
            onClick={create}
          >
            {canCreate ? "Create RD-Agent run" : "Create run — not enabled"}
          </button>
        </>
      )}
    </div>
  );
}
