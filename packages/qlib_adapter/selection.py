"""Freeze training-label admission before learner invocation; no inference authority."""

from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field

from packages.factors.contracts import Contract, DateWindow, digest

from .contracts import (
    LabelManifest,
    LabelRow,
    NativeBinding,
    NativeEvidenceManifest,
    NativePortfolioResult,
    NativePredictionResult,
)


class ExcludedLabel(Contract):
    row: LabelRow
    reason: Literal[
        "outside_fit_windows", "not_available_at_fit_cutoff", "missing_label"
    ]


class FitSelection(Contract):
    schema_version: Literal["mineagent.qlib-fit-label-selection.v1"] = (
        "mineagent.qlib-fit-label-selection.v1"
    )
    label_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    training_window: DateWindow
    validation_window: DateWindow
    training_fit_cutoff: datetime
    validation_selection_cutoff: datetime
    training: tuple[LabelRow, ...] = Field(min_length=1, max_length=1_000_000)
    validation: tuple[LabelRow, ...] = Field(min_length=1, max_length=1_000_000)
    excluded: tuple[ExcludedLabel, ...] = Field(max_length=1_000_000)
    policy: Literal["exclude_unavailable_labels_before_fit"] = (
        "exclude_unavailable_labels_before_fit"
    )

    @property
    def sha256(self):
        return digest(self)


def select_fit_labels(
    binding: NativeBinding,
    manifest: LabelManifest,
    rows: tuple[LabelRow, ...],
    *,
    calendar_sessions: tuple[date, ...],
):
    """Only labels complete and available before the relevant fit/selection cutoff.

    The returned selection must be frozen *before* constructing DatasetH. A
    downstream caller must join its exact accepted keys, never the original rows.
    """
    binding = NativeBinding.model_validate(binding)
    manifest = LabelManifest.model_validate(manifest)
    rows = tuple(LabelRow.model_validate(row) for row in rows)
    if (
        manifest.dataset_id != binding.dataset_id
        or manifest.dataset_sha256 != binding.dataset_sha256
        or manifest.sha256 != binding.label_manifest_sha256
    ):
        raise ValueError("Fit labels substitute bound dataset or label manifest")
    if (
        len(rows) != manifest.row_count
        or digest([r.model_dump(mode="json") for r in rows]) != manifest.rows_sha256
    ):
        raise ValueError("Fit label rows differ from frozen manifest")
    if (
        tuple(sorted(set(calendar_sessions))) != calendar_sessions
        or digest([d.isoformat() for d in calendar_sessions])
        != manifest.calendar_sessions_sha256
    ):
        raise ValueError("Fit calendar differs from label manifest")
    positions = {day: index for index, day in enumerate(calendar_sessions)}
    zone = ZoneInfo(manifest.market_timezone)
    for row in rows:
        pos = positions.get(row.session)
        if (
            pos is None
            or pos + manifest.horizon_sessions >= len(calendar_sessions)
            or calendar_sessions[pos + manifest.horizon_sessions] != row.horizon_end
            or row.horizon_end_time.astimezone(zone).date() != row.horizon_end
        ):
            raise ValueError("Fit label horizon differs from frozen calendar")
    keys = [(r.session, r.listing_id) for r in rows]
    if keys != sorted(set(keys)):
        raise ValueError("Fit labels must be sorted and unique")
    zone = ZoneInfo(manifest.market_timezone)
    if binding.training_fit_cutoff >= datetime.combine(
        binding.validation_window.start, time.min, tzinfo=zone
    ) or binding.validation_selection_cutoff >= datetime.combine(
        binding.test_window.start, time.min, tzinfo=zone
    ):
        raise ValueError("Label selection cutoff enters the next evaluation window")
    accepted = {"train": [], "valid": []}
    excluded = []
    for row in rows:
        segment = (
            "train"
            if binding.training_window.start
            <= row.session
            < binding.training_window.end
            else "valid"
            if binding.validation_window.start
            <= row.session
            < binding.validation_window.end
            else None
        )
        reason = None
        if segment is None:
            reason = "outside_fit_windows"
        else:
            cutoff = (
                binding.training_fit_cutoff
                if segment == "train"
                else binding.validation_selection_cutoff
            )
            if row.horizon_end_time > cutoff or row.available_at > cutoff:
                reason = "not_available_at_fit_cutoff"
            elif row.value is None:
                reason = "missing_label"
        if reason:
            excluded.append(ExcludedLabel(row=row, reason=reason))
        else:
            accepted[segment].append(row)
    return FitSelection(
        label_manifest_sha256=manifest.sha256,
        training_window=binding.training_window,
        validation_window=binding.validation_window,
        training_fit_cutoff=binding.training_fit_cutoff,
        validation_selection_cutoff=binding.validation_selection_cutoff,
        training=tuple(accepted["train"]),
        validation=tuple(accepted["valid"]),
        excluded=tuple(excluded),
    )


def validate_native_results(
    manifest: NativeEvidenceManifest,
    prediction: NativePredictionResult,
    portfolio: NativePortfolioResult,
    selection: FitSelection,
    labels: LabelManifest,
    *,
    calendar_sessions: tuple[date, ...],
):
    """Fail closed on cross-run, report, selection or namespace substitution."""
    manifest = NativeEvidenceManifest.model_validate(manifest)
    prediction = NativePredictionResult.model_validate(prediction)
    portfolio = NativePortfolioResult.model_validate(portfolio)
    selection = FitSelection.model_validate(selection)
    labels = LabelManifest.model_validate(labels)
    all_rows = tuple(
        sorted(
            (
                *selection.training,
                *selection.validation,
                *(x.row for x in selection.excluded),
            ),
            key=lambda row: (row.session, row.listing_id),
        )
    )
    expected_selection = select_fit_labels(
        manifest.binding, labels, all_rows, calendar_sessions=calendar_sessions
    )
    if expected_selection != selection:
        raise ValueError(
            "Fit selection accepted unavailable labels or changed exclusions"
        )
    binding_sha = digest(manifest.binding)
    if (
        prediction.native_binding_sha256 != binding_sha
        or portfolio.native_binding_sha256 != binding_sha
    ):
        raise ValueError("Native results substitute run binding")
    if portfolio.daily_report_sha256 != manifest.binding.daily_report_sha256:
        raise ValueError("Portfolio substitutes actual daily report")
    if selection.sha256 != manifest.binding.fit_selection_sha256:
        raise ValueError("Native result substitutes frozen fit label selection")
    if selection.label_manifest_sha256 != manifest.binding.label_manifest_sha256:
        raise ValueError("Fit selection substitutes labels")
    for day in prediction.days + portfolio.days:
        if (
            not manifest.binding.test_window.start
            <= day.session
            < manifest.binding.test_window.end
        ):
            raise ValueError("Native result day is outside frozen test window")
    return True
