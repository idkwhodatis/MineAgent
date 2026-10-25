import type { ExposureSnapshot, SplitVersionPublic } from "@/client/types.gen";
import { JsonDetails, Metadata } from "@/components/data-common";
import { Panel } from "@/components/common";
import { label } from "@/lib/format";
import { exposureNotice } from "./protocol";

export function exposureClassificationNotice(
  classification: string,
): string | null {
  return classification === "unknown_import_history"
    ? "Imported factor history is incomplete or unresolved. No matching local record does not mean these data are unseen; do not treat this interval as a clean holdout."
    : null;
}

export function ExposurePanel({
  snapshot,
  title = "Platform-recorded prior exposure",
}: {
  snapshot: ExposureSnapshot;
  title?: string;
}) {
  const classificationNotice = exposureClassificationNotice(
    snapshot.classification,
  );
  return (
    <Panel title={title}>
      <p className="notice" data-testid="holdout-exposure-warning">
        {exposureNotice}
      </p>
      {classificationNotice && (
        <p className="notice" data-testid="import-history-warning">
          {classificationNotice}
        </p>
      )}
      <Metadata
        values={{
          "Tracking classification": label(snapshot.classification),
          "Matching exposure records": snapshot.matching_count,
          "Recorded purposes":
            snapshot.matching_purposes.join(", ") || "None recorded",
          "As of": snapshot.as_of,
          "Tracking coverage": snapshot.tracking_coverage,
          "Evidence truncated": snapshot.evidence_truncated,
        }}
      />
      <JsonDetails
        title="Exposure record IDs and identity evidence"
        value={{
          exposure_ids: snapshot.matching_exposure_ids,
          identities: snapshot.identities,
        }}
      />
    </Panel>
  );
}
export function SplitPanel({ split }: { split: SplitVersionPublic }) {
  return (
    <Panel title="Immutable train, validation and holdout split">
      <Metadata
        values={{
          Experiment: split.experiment_id,
          "Split version": split.id,
          "Split SHA-256": split.content_sha256,
          "Dataset version": split.definition.dataset.id,
          "Dataset SHA-256": split.definition.dataset.sha256,
          "Universe version": split.definition.universe.id,
          "Universe SHA-256": split.definition.universe.sha256,
          "Train [start, end)": split.definition.windows.train,
          "Validation [start, end)": split.definition.windows.validation,
          "Holdout [start, end)": split.definition.windows.holdout,
        }}
      />
      <p className="text-xs muted mt-4">
        Every window uses an inclusive start and exclusive end. Final evaluation
        uses only the frozen holdout, never a replacement validation window.
      </p>
    </Panel>
  );
}
