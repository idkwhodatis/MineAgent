import { InputContentActions } from "./input-content";
import type { InputDerivationView } from "@/input-audit-contracts/types.gen";
const statusLabels: Record<InputDerivationView["status"], string> = {
  prepared: "Prepared input",
  started_no_terminal_receipt: "Start recorded; no terminal receipt",
  succeeded: "Derivation completed",
  rejected: "Derivation rejected",
  failed: "Derivation failed",
  cancelled: "Derivation cancelled",
  interrupted: "Derivation interrupted",
};
export function InputDerivationAudit({
  value,
}: {
  value: InputDerivationView;
}) {
  return (
    <section aria-label="Input derivation audit">
      <h3>
        {value.operation === "external_normalization"
          ? "External proposal normalization"
          : "Experiment feedback projection"}
      </h3>
      <p>{statusLabels[value.status]}</p>
      <p>
        Retained receipt metadata, not live worker status, model consumption,
        approval or execution permission.
      </p>
      {value.status === "started_no_terminal_receipt" && (
        <p role="status">
          No terminal receipt has been recorded. This does not prove that
          processing is still running or that any output exists.
        </p>
      )}
      {value.operation === "external_normalization" && (
        <p>
          Normalization checks format and bounds only. External content remains
          untrusted data, never instructions or human approval.
        </p>
      )}
      <dl>
        <dt>Workflow</dt>
        <dd>{value.workflow_id}</dd>
        <dt>Stage</dt>
        <dd>{value.stage_id}</dd>
        <dt>Derivation receipt</dt>
        <dd>{value.derivation_id}</dd>
        <dt>Prepared</dt>
        <dd>{value.prepared_at}</dd>
        <dt>Start receipt</dt>
        <dd>{value.started_at ?? "Not recorded"}</dd>
        <dt>Terminal receipt</dt>
        <dd>{value.terminal_at ?? "Not recorded"}</dd>
        <dt>Source commitment SHA-256</dt>
        <dd className="break-all">{value.source_sha256}</dd>
        <dt>Raw external input SHA-256</dt>
        <dd className="break-all">
          {value.raw_input_sha256 ?? "Not applicable"}
        </dd>
        <dt>Mapping SHA-256</dt>
        <dd className="break-all">
          {value.mapping_sha256 ?? "Not applicable"}
        </dd>
      </dl>
      {value.operation === "external_normalization" &&
        value.raw_object_id &&
        value.raw_input_sha256 && (
          <InputContentActions
            reference={{
              kind: "raw",
              id: value.raw_object_id,
              sha256: value.raw_input_sha256,
            }}
          />
        )}
      {value.error_code && (
        <p role="alert">Recorded error category: {value.error_code}</p>
      )}
      <h4>Actual derived version references</h4>
      {!value.outputs.length && (
        <p>
          No derived version references recorded. No ready input has been
          fabricated.
        </p>
      )}
      <ol>
        {value.outputs.map((ref) => (
          <li key={`${ref.origin}:${ref.version_id}`}>
            <p>
              {ref.origin === "normalized_external_proposal"
                ? "Normalized external proposal"
                : ref.origin === "experiment_feedback_proposal"
                  ? "Experiment feedback proposal"
                  : "Unexpected origin; not eligible for selection"}
            </p>
            <p>
              Record {ref.record_id}; version {ref.version_id}.
            </p>
            {(ref.origin === "normalized_external_proposal" ||
              ref.origin === "experiment_feedback_proposal") && (
              <InputContentActions
                reference={{
                  kind:
                    ref.origin === "experiment_feedback_proposal"
                      ? "feedback"
                      : "normalized",
                  id: ref.version_id,
                  sha256: ref.content_sha256,
                }}
              />
            )}
            <p className="break-all">
              Exact content SHA-256: {ref.content_sha256}
            </p>
          </li>
        ))}
      </ol>
      <p>
        Raw content and normalized text are private by default. A reference is
        not a selection, a submitted model request or permission to reveal
        sealed result values. Manual user guidance remains a separate pinned
        source.
      </p>
    </section>
  );
}
