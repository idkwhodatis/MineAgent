import { deploymentUrl } from "@/lib/deployment";
import type {
  StructuredModelOption,
  StructuredSnapshotOption,
} from "@/client/types.gen";

/** Metadata choices are not fresh eligibility or authority to submit. */
export function OEProviderSelection({
  snapshots,
  models,
  snapshotId,
  modelProfileId,
  disabled = false,
  modelOnly = false,
  onSnapshot,
  onModel,
}: {
  snapshots: StructuredSnapshotOption[];
  models: StructuredModelOption[];
  snapshotId: string;
  modelProfileId: string;
  disabled?: boolean;
  modelOnly?: boolean;
  onSnapshot: (id: string) => void;
  onModel: (id: string) => void;
}) {
  const snapshot = snapshots.find((value) => value.snapshot_id === snapshotId);
  const model = models.find(
    (value) => value.binding.profile_id === modelProfileId,
  );
  return (
    <fieldset disabled={disabled}>
      <legend>Actual released factor and operator model</legend>
      <p>
        This profile uses a fixed retained synthetic or explicitly user-declared
        upload market, not independently verified live market or point-in-time
        evidence. The selected operator model is labeled separately. No model or
        factor is selected automatically.
      </p>
      {!modelOnly && (
        <>
          <label>
            Released execution snapshot
            <select
              required
              value={snapshotId}
              onChange={(event) => onSnapshot(event.target.value)}
            >
              <option value="">Select an explicitly released snapshot</option>
              {snapshotId && !snapshot && (
                <option value={snapshotId}>
                  Saved selection unavailable: {snapshotId}
                </option>
              )}
              {snapshots.map((value) => (
                <option key={value.snapshot_id} value={value.snapshot_id}>
                  {value.factor_version_id} · {value.snapshot_id}
                </option>
              ))}
            </select>
          </label>
          {snapshotId && !snapshot && (
            <p role="alert">
              The exact saved snapshot is not in this page. Load its page or
              explicitly choose another; it has not been replaced.
            </p>
          )}
          {snapshot && (
            <section aria-label="Selected released factor metadata">
              <p>
                Factor {snapshot.factor_id}; version{" "}
                {snapshot.factor_version_id}; retained dataset{" "}
                {snapshot.dataset_version_id}.
              </p>
              <p>
                Signal window: {snapshot.signal_window.start} inclusive to{" "}
                {snapshot.signal_window.end} exclusive. This is not an
                automatically chosen execution window.
              </p>
              <p className="break-all">
                Snapshot manifest SHA-256: {snapshot.snapshot_manifest_sha256}
              </p>
              <p>
                Existing release receipt: {snapshot.release_receipt_id}. Fresh
                preflight is still required.
              </p>
              <a
                href={deploymentUrl(
                  `/factors/${snapshot.factor_id}/versions/${snapshot.factor_version_id}`,
                )}
              >
                Inspect exact factor version
              </a>{" "}
              <a
                href={deploymentUrl(
                  `/engine-runs/${snapshot.source_run_id}/snapshots/${snapshot.snapshot_id}`,
                )}
              >
                Inspect released source snapshot
              </a>
            </section>
          )}
        </>
      )}
      <label>
        Operator model profile
        <select
          required
          value={modelProfileId}
          onChange={(event) => onModel(event.target.value)}
        >
          <option value="">
            Select an explicitly configured operator model
          </option>
          {modelProfileId && !model && (
            <option value={modelProfileId}>
              Saved profile unavailable: {modelProfileId}
            </option>
          )}
          {models.map((value) => (
            <option
              key={value.binding.profile_id}
              value={value.binding.profile_id}
              disabled={!value.operator_available}
            >
              {value.label} · {value.binding.model}
              {!value.operator_available
                ? ` — ${value.unavailable_reason}`
                : ""}
            </option>
          ))}
        </select>
      </label>
      {modelProfileId && !model && (
        <p role="alert">
          The exact model profile is unavailable. Do not substitute a model for
          an unresolved submission.
        </p>
      )}
      {model && (
        <section aria-label="Selected operator model metadata">
          <p>
            {model.binding.synthetic
              ? "Synthetic model transport"
              : "Operator model transport"}
            ; model {model.binding.model}. Operator availability is not
            permission to submit.
          </p>
          {!model.operator_available && (
            <p role="alert">Unavailable: {model.unavailable_reason}</p>
          )}
          <p className="break-all">
            Exact model binding SHA-256: {model.binding_sha256}
          </p>
          <p>
            Pricing: {model.binding.pricing?.status ?? "unavailable"}. Billing
            remains unknown; no provider credential or endpoint is supplied by
            this form.
          </p>
        </section>
      )}
    </fieldset>
  );
}
