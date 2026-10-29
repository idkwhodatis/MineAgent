"""Cold exact local exposure links; incomplete external history remains unknown."""

import re
from datetime import UTC, datetime
from types import SimpleNamespace

from backend.app.experiment_services import dataset_identities
from backend.app.rdagent_configured_exposure import PURPOSE, exposure_provenance
from packages.factors.contracts import SnapshotInput, canonical_bytes, digest

EXPOSURE_FIELDS = "id owner_id experiment_id split_version_id run_id dataset_version_id source_key identities start_date end_date purpose provenance created_at"


def _retained_exposure_digest(row):
    """Rebuild only the ORM timestamp rendering committed by the live writer.

    PostgreSQL to_jsonb(timestamptz) trims trailing fractional zeroes, whereas
    datetime.isoformat emits six digits for nonzero microseconds. Other fields
    remain untouched; this is not a generic JSON/string coercion or hash fallback.
    """
    stamp = row["created_at"]
    if (
        type(stamp) is not str
        or re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?\+00:00",
            stamp,
        )
        is None
    ):
        raise ValueError("Exact UTC exposure timestamp required")
    parsed = datetime.fromisoformat(stamp)
    if parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("Exact UTC exposure timestamp required")
    return digest({**row, "created_at": parsed.isoformat()})


def validate_configured_exposure_links(
    plans, datasets, exposures, evaluations, *, maximum_rows
):
    if type(maximum_rows) is not int or maximum_rows < 1:
        raise ValueError("Exposure bound")
    if any(
        type(rows) is not list or len(rows) > maximum_rows
        for rows in (plans, datasets, exposures, evaluations)
    ):
        raise ValueError("Exposure rows")
    by_id = {row["id"]: row for row in exposures}
    if len(by_id) != len(exposures):
        raise ValueError("Duplicate exposure")
    for row in exposures:
        if set(row) != set(EXPOSURE_FIELDS.split()):
            raise ValueError("Exposure columns")
    data = {row["id"]: row for row in datasets}
    if len(data) != len(datasets):
        raise ValueError("Duplicate exposure dataset")
    for plan in plans:
        dataset = data[plan["label_source_dataset_id"]]
        candidates = [
            row
            for row in exposures
            if row["owner_id"] == plan["owner_id"]
            and row["source_key"] == "rd-configured-source:" + plan["id"]
        ]
        if len(candidates) != 1:
            raise ValueError("Configured admission exposure missing")
        admission = candidates[0]
        if (
            admission["run_id"],
            admission["dataset_version_id"],
            admission["purpose"],
            admission["start_date"],
            admission["end_date"],
            admission["provenance"],
        ) != (
            plan["run_id"],
            dataset["id"],
            PURPOSE,
            dataset["start_date"],
            dataset["end_date"],
            exposure_provenance(SimpleNamespace(**plan)),
        ):
            raise ValueError("Configured exposure binding")
        if admission["identities"] != dataset_identities(SimpleNamespace(**dataset)):
            raise ValueError("Configured exposure identity coverage")
        if (
            dataset["owner_id"] != plan["owner_id"]
            or dataset["content_sha256"] != plan["label_source_manifest_sha256"]
        ):
            raise ValueError("Configured exposure dataset")
        for evaluation in evaluations:
            if (evaluation["owner_id"], evaluation["run_id"]) != (
                plan["owner_id"],
                plan["run_id"],
            ):
                continue
            snapshot = SnapshotInput.model_validate_json(
                canonical_bytes(evaluation["snapshot_input"])
            )
            claims = {
                claim.evidence_id: claim for claim in snapshot.exposure_history.claims
            }
            if (
                len(claims) != len(snapshot.exposure_history.claims)
                or admission["id"] not in claims
            ):
                raise ValueError("Frozen configured exposure absent")
            for claim in claims.values():
                if claim.origin_namespace != "mineagent.local.exposure":
                    continue
                row = by_id.get(claim.evidence_id)
                if (
                    row is None
                    or row["owner_id"] != plan["owner_id"]
                    or claim.evidence_sha256 != _retained_exposure_digest(row)
                    or claim.purpose != row["purpose"]
                    or claim.window.model_dump(mode="json")
                    != {"start": row["start_date"], "end": row["end_date"]}
                ):
                    raise ValueError("Frozen local exposure identity differs")
            if snapshot.exposure_history.completeness != "partial":
                raise ValueError(
                    "Unverified source cannot imply complete exposure history"
                )
