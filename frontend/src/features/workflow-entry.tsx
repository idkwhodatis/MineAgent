import { emptyOEDeclaration } from "./oe-provider-entry";
import { WorkflowStructuredChoice } from "./workflow-structured-choice";
import type {
  AnyWorkflowRequest,
  AnyWorkflowPreflight,
} from "@/lib/research-workflows";
import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ErrorState, Panel } from "@/components/common";
import { EngineEntryForm } from "./engine-preflight";
import { RawExternalIntake } from "./raw-external-intake";
import { InputContentActions } from "./input-content";
import type { EngineRequest } from "@/lib/engine-runs-api";
import type { RetainedExternalInput } from "@/input-audit-contracts/types.gen";
import type {
  InputVersionRef,
  WorkflowCreated,
  WorkflowRequest,
} from "@/workflow-contracts/types.gen";
import {
  loadWorkflowEnvelope,
  preflightWorkflow,
  prepareWorkflowEnvelope,
  readWorkflowManualOptions,
  submitWorkflowEnvelope,
} from "@/lib/research-workflows";

/** Availability comes from the installed server capability, never UI presence. */
export function WorkflowEntry({
  ownerId,
  available = false,
  structuredAvailable = false,
  reason,
  onCreated,
  proposalOptions = [],
}: {
  ownerId: string;
  available?: boolean;
  structuredAvailable?: boolean;
  reason: string;
  onCreated: (receipt: WorkflowCreated) => void;
  proposalOptions?: { ref: InputVersionRef; title: string }[];
}) {
  const [structured, setStructured] = useState(false);
  const [oe, setOE] = useState(emptyOEDeclaration);
  const [totalConfirmed, setTotalConfirmed] = useState(false);
  const [research, setResearch] = useState<WorkflowRequest["research"] | null>(
    null,
  );
  const [ceiling, setCeiling] = useState<
    WorkflowRequest["budget_ceiling"] | null
  >(null);
  const [refs, setRefs] = useState<InputVersionRef[]>([]);
  const [mode, setMode] = useState<WorkflowRequest["mode"]>("continuous");
  const [iterations, setIterations] = useState(1);
  const [feedback, setFeedback] = useState(false);
  const [raw, setRaw] = useState<RetainedExternalInput | null>(null);
  const [itemsPath, setItemsPath] = useState('["items"]');
  const [textPath, setTextPath] = useState('["text"]');
  const [titlePath, setTitlePath] = useState('["title"]');
  const [checked, setChecked] = useState<{
    request: AnyWorkflowRequest;
    result: AnyWorkflowPreflight;
  } | null>(null);
  const [saved, setSaved] = useState(() => loadWorkflowEnvelope(ownerId));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const busy = useRef(false);
  const active = useRef(true);
  const [accepted, setAccepted] = useState<WorkflowCreated | null>(null);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  const options = useQuery({
    queryKey: ["workflow-manual-inputs", ownerId],
    queryFn: ({ signal }) => readWorkflowManualOptions(0, signal),
    enabled: available,
    retry: false,
  });
  const invalidate = () => {
    setChecked(null);
    setError(null);
  };
  const declaration = useCallback((value: EngineRequest | null) => {
    setChecked(null);
    setTotalConfirmed(false);
    if (
      value?.schema_version === "mineagent.rdagent-provider-submission.v1" ||
      value?.schema_version ===
        "mineagent.rdagent-provider-seeded-submission.v1"
    ) {
      setResearch({
        ...value,
        schema_version: value.schema_version,
      } as WorkflowRequest["research"]);
      setCeiling(value.budget);
    } else {
      setResearch(null);
      setCeiling(null);
    }
  }, []);
  const locked = pending || saved.state !== "empty" || Boolean(accepted);
  function toggle(ref: InputVersionRef) {
    invalidate();
    setRefs((previous) =>
      previous.some(
        (v) => v.origin === ref.origin && v.version_id === ref.version_id,
      )
        ? previous.filter(
            (v) =>
              !(v.origin === ref.origin && v.version_id === ref.version_id),
          )
        : [
            ...previous.filter(
              (v) =>
                !(v.origin === ref.origin && v.record_id === ref.record_id),
            ),
            ref,
          ],
    );
  }
  async function check() {
    if (!research || !ceiling || locked || !available || busy.current) return;
    busy.current = true;
    setPending(true);
    setError(null);
    setChecked(null);
    try {
      if (structured && (!structuredAvailable || !totalConfirmed))
        throw new Error(
          "Explicitly confirm the total RD and OE budget before checking.",
        );
      const base: WorkflowRequest = {
        schema_version: "mineagent.workflow-public-request.v1",
        research,
        mode,
        maximum_iterations: iterations,
        budget_ceiling: ceiling,
        refs,
        allow_workflow_development_feedback_release: feedback,
        external_definition: raw
          ? {
              schema_version: "mineagent.external-normalization-stage.v1",
              raw_object_id: raw.id,
              raw_sha256: raw.sha256,
              mapping: {
                schema_version: "mineagent.external-feed-mapping.v1",
                items_path: JSON.parse(itemsPath),
                text_path: JSON.parse(textPath),
                title_path:
                  titlePath.trim() === "null" ? null : JSON.parse(titlePath),
                kind: "direction",
              },
            }
          : null,
      };
      const request: AnyWorkflowRequest = structured
        ? {
            ...base,
            schema_version: "mineagent.workflow-public-request.v2",
            structured: {
              seed_parameters: oe.seed_parameters,
              objective: oe.objective,
              budget: oe.budget,
              maximum_mutation_slots: oe.maximum_mutation_slots,
              model_profile_id: oe.model_profile_id,
              model_binding_sha256: oe.model_binding_sha256,
            },
          }
        : base;
      const result = await preflightWorkflow(request);
      if (active.current) setChecked({ request, result });
    } catch (failure) {
      setError(failure);
    } finally {
      busy.current = false;
      setPending(false);
    }
  }
  async function submit() {
    if (busy.current || accepted || saved.state === "invalid") return;
    const envelope =
      saved.state === "pending"
        ? saved.envelope
        : available && checked
          ? prepareWorkflowEnvelope(ownerId, checked.request, checked.result)
          : null;
    if (!envelope) return;
    busy.current = true;
    setPending(true);
    setError(null);
    try {
      const receipt = await submitWorkflowEnvelope(envelope);
      if (active.current) {
        setAccepted(receipt);
        onCreated(receipt);
      }
    } catch (failure) {
      setError(failure);
    } finally {
      setSaved(loadWorkflowEnvelope(ownerId));
      busy.current = false;
      setPending(false);
    }
  }
  return (
    <section aria-label="Finite research workflow entry">
      <h2>Research workflow</h2>
      <p>
        Immutable stage inputs; completed and running stages are never silently
        rewritten. Continuous mode does not require human approval at each
        stage.
      </p>
      {accepted && (
        <p role="status">
          Original creation receipt: {accepted.workflow_id} · {accepted.status}.
          Read current history separately; this is not a child launch
          acknowledgement.
        </p>
      )}
      {!available && (
        <p role="status">Workflow creation unavailable: {reason}</p>
      )}
      {saved.state !== "empty" && (
        <Panel title="Unresolved workflow submission">
          <p>
            Keep the original request and key. Reload, authentication errors,
            CAS conflicts and timeouts do not prove rejection. Editing remains
            locked.
          </p>
          <button
            disabled={pending || saved.state !== "pending"}
            onClick={() => void submit()}
          >
            Retry exact workflow request
          </button>
        </Panel>
      )}
      <fieldset disabled={locked || !available}>
        <EngineEntryForm
          ownerId={ownerId}
          initialParent=""
          workflowDeclaration={declaration}
        />
        <label>
          <input
            type="checkbox"
            checked={structured}
            disabled={!structuredAvailable}
            onChange={(event) => {
              setStructured(event.target.checked);
              setTotalConfirmed(false);
              invalidate();
            }}
          />
          Add one terminal operator-backed structured OE stage (opt-in)
        </label>
        {!structuredAvailable && (
          <p>
            Structured workflow preflight is unavailable on the installed
            server; the existing RD workflow remains the default.
          </p>
        )}
        {structured && (
          <WorkflowStructuredChoice
            ownerId={ownerId}
            request={oe}
            onChange={(value) => {
              setOE(value);
              setTotalConfirmed(false);
              invalidate();
            }}
          />
        )}
        <Panel title="User-provided manual guidance — pinned first">
          <p>
            Recorded manual input, not model compliance or approval. Saving a
            steer is separate from applying a future workflow selection.
          </p>
          {options.error && <ErrorState error={options.error} />}
          {options.data?.items.map((option) => (
            <label key={option.ref.version_id}>
              <input
                type="checkbox"
                name="workflow-manual-version"
                value={option.ref.version_id}
                disabled={option.disabled}
                checked={refs.some(
                  (v) => v.version_id === option.ref.version_id,
                )}
                onChange={() => toggle(option.ref)}
              />
              {option.version.kind}: {option.version.text} · version{" "}
              {option.version.ordinal}
              {option.disabled ? " (disabled for new selection)" : ""}
            </label>
          ))}
          {options.data?.has_more && (
            <p>
              Only the first 100 manual records are shown; no unseen record is
              selected automatically.
            </p>
          )}
        </Panel>
        <Panel title="Agent-generated proposals — separate origin">
          <p>
            Internal feedback and normalized external proposals retain their
            provenance; displaying or selecting one is not human approval.
          </p>
          {!proposalOptions.length && (
            <p>
              No actual ready proposal references supplied by the current
              workflow history.
            </p>
          )}
          {proposalOptions.map((option) => (
            <label key={`${option.ref.origin}:${option.ref.version_id}`}>
              <input
                type="checkbox"
                checked={refs.some(
                  (v) => v.version_id === option.ref.version_id,
                )}
                disabled={
                  option.ref.origin === "unknown" ||
                  option.ref.origin === "manual_user_submission"
                }
                onChange={() => toggle(option.ref)}
              />
              {option.ref.origin}: {option.title}
            </label>
          ))}
        </Panel>
        <h3>Exact ordered selection for the next claimed stage</h3>
        <ol>
          {refs.map((ref, index) => (
            <li key={`${ref.origin}:${ref.version_id}`}>
              {index + 1}. {ref.origin} · {ref.version_id}
              <button
                type="button"
                disabled={index === 0}
                onClick={() => {
                  invalidate();
                  setRefs((old) => {
                    const next = [...old];
                    [next[index - 1], next[index]] = [
                      next[index],
                      next[index - 1],
                    ];
                    return next;
                  });
                }}
              >
                Move earlier
              </button>
            </li>
          ))}
        </ol>
        <p>
          Display pinning does not reorder prompt inputs. Oversized combined
          guidance is rejected, never silently truncated.
        </p>
        <RawExternalIntake
          canRetain={available && !locked}
          reason={reason}
          onRetained={(value) => {
            setRaw(value);
            invalidate();
          }}
        />
        {raw && (
          <>
            <InputContentActions
              reference={{ kind: "raw", id: raw.id, sha256: raw.sha256 }}
            />
            <p>
              Retained raw input is not normalized yet. Mapping uses literal
              JSON keys/index arrays, never executable JSONPath.
            </p>
            <label>
              Items path
              <input
                value={itemsPath}
                onChange={(e) => {
                  setItemsPath(e.target.value);
                  invalidate();
                }}
              />
            </label>
            <label>
              Text path
              <input
                value={textPath}
                onChange={(e) => {
                  setTextPath(e.target.value);
                  invalidate();
                }}
              />
            </label>
            <label>
              Title path or null
              <input
                value={titlePath}
                onChange={(e) => {
                  setTitlePath(e.target.value);
                  invalidate();
                }}
              />
            </label>
            <button
              type="button"
              onClick={() => {
                setRaw(null);
                invalidate();
              }}
            >
              Do not attach this retained input
            </button>
          </>
        )}
        <label htmlFor="workflow-mode">Mode</label>
        <select
          id="workflow-mode"
          value={mode}
          onChange={(e) => {
            setMode(e.target.value as WorkflowRequest["mode"]);
            invalidate();
          }}
        >
          <option value="continuous">
            Continuous within the finite budget
          </option>
          <option value="pause_after_stage">Pause after stage</option>
        </select>
        <label>
          Maximum iterations
          <input
            type="number"
            min={1}
            max={8}
            value={iterations}
            onChange={(e) => {
              setIterations(Number(e.target.value));
              setTotalConfirmed(false);
              invalidate();
            }}
          />
        </label>
        <p>
          The workflow ceiling initially equals one child declaration budget; it
          is never automatically multiplied. Adjust explicitly before preflight.
        </p>
        {ceiling &&
          Object.entries(ceiling).map(([name, value]) => (
            <label key={name}>
              Workflow budget {name}
              <input
                type="number"
                min={0}
                step={1}
                value={value}
                onChange={(event) => {
                  invalidate();
                  setTotalConfirmed(false);
                  setCeiling((old) =>
                    old ? { ...old, [name]: Number(event.target.value) } : old,
                  );
                }}
              />
            </label>
          ))}
        {structured && (
          <label>
            <input
              type="checkbox"
              checked={totalConfirmed}
              onChange={(event) => {
                setTotalConfirmed(event.target.checked);
                invalidate();
              }}
            />
            I explicitly set the total workflow budget to cover all RD
            iterations and the OE child. No budget is automatically raised.
          </label>
        )}
        <label>
          <input
            type="checkbox"
            checked={feedback}
            onChange={(e) => {
              setFeedback(e.target.checked);
              invalidate();
            }}
          />
          Allow this workflow’s eligible owned development outputs to be
          released through the existing ledger for feedback. Excludes final,
          independent and unknown outputs; not global or retroactive.
        </label>
        <button
          type="button"
          disabled={
            !research ||
            pending ||
            (structured && (!structuredAvailable || !totalConfirmed))
          }
          onClick={() => void check()}
        >
          Check complete workflow
        </button>
        <button
          type="button"
          disabled={!checked || pending}
          onClick={() => void submit()}
        >
          Create exact workflow request
        </button>
      </fieldset>
      {checked && (
        <p>
          Preflight commitment: {checked.result.preflight_sha256}. No child was
          submitted or guidance consumed by this check.
        </p>
      )}
      {error !== null && <ErrorState error={error} />}
    </section>
  );
}
