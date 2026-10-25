"""Trusted-host projection of exact factor snapshots, never a candidate clock.

The host constructs DecisionContext and keeps this object outside the sandbox.
Candidates may request allowed factor IDs, not context, paths, cutoff or receipts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Literal
from uuid import UUID, uuid5

from pydantic import Field, field_validator, model_validator

from .contracts import (
    Contract,
    Count,
    FactorRunBinding,
    FeatureRow,
    FeatureSnapshotManifest,
    FittedPreprocessing,
    Identifier,
    LocalVerification,
    Sha256,
    StageCall,
    StageReply,
    aware_utc,
    canonical_bytes,
    digest,
    row_key,
    rows_digest,
    validate_history_inheritance,
    validate_rows,
    validate_stage_reply,
)

_RECEIPTS = UUID("c599d9a4-157a-54e4-a64a-188512911398")
MAX_PROVIDER_ROWS = 100_000
MAX_PROVIDER_BYTES = 128 * 1024 * 1024
MAX_RECEIPTS = 100_000
MAX_VIEW_ROWS = 10_000
MAX_VIEW_BYTES = 4 * 1024 * 1024


class DecisionContext(Contract):
    decision_id: Identifier
    event_seq: Count
    decision_time: datetime
    information_cutoff: datetime
    stage_id: Identifier
    listing_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=100)

    _times = field_validator("decision_time", "information_cutoff")(aware_utc)

    @model_validator(mode="after")
    def bounded_context(self):
        if self.information_cutoff > self.decision_time:
            raise ValueError("Host information cutoff cannot exceed decision time")
        if len(set(self.listing_ids)) != len(self.listing_ids):
            raise ValueError("Decision listings must be unique")
        return self


@dataclass(frozen=True)
class VerifiedSnapshot:
    """Construct only after trusted storage admission; verification isn't imported."""

    manifest: FeatureSnapshotManifest
    rows: tuple[FeatureRow, ...]
    verification: LocalVerification


class AccessReceipt(Contract):
    schema_version: Literal["mineagent.factor-access-receipt.v1"] = (
        "mineagent.factor-access-receipt.v1"
    )
    receipt_id: UUID
    level: Literal["provided", "read_requested"]
    source: Literal["host_observed"] = "host_observed"
    decision_id: Identifier
    event_seq: Count
    stage_id: Identifier
    information_cutoff: datetime
    run_binding_sha256: Sha256
    factor_version_id: UUID
    snapshot_id: UUID
    output_digest: Sha256
    listing_ids: tuple[Identifier, ...]
    history_sessions: Count
    rows_digest: Sha256
    row_count: Count
    temporal_verification: Literal[
        "declared_or_unknown", "fixed_fixture_recomputed", "verified_source"
    ]
    causal_contribution: Literal["not_established"] = "not_established"

    _time = field_validator("information_cutoff")(aware_utc)


class UnavailableValue(Contract):
    factor_version_id: UUID
    listing_id: Identifier
    reason: Literal["not_available_at_cutoff", "outside_calculation_universe"]


@dataclass(frozen=True)
class FeatureView:
    rows: tuple[FeatureRow, ...]
    receipts: tuple[AccessReceipt, ...]
    unavailable: tuple[UnavailableValue, ...]


class FeatureProvider:
    def __init__(
        self, binding: FactorRunBinding, snapshots: tuple[VerifiedSnapshot, ...]
    ):
        by_id = {item.manifest.snapshot_id: item for item in snapshots}
        if len(by_id) != len(snapshots) or set(by_id) != {
            v.snapshot_id for v in binding.snapshots
        }:
            raise ValueError("Provider requires exactly the frozen snapshot inventory")
        selected = {}
        indexed = {}
        total_rows = total_bytes = 0
        for pinned in binding.snapshots:
            item = by_id[pinned.snapshot_id]
            manifest = item.manifest
            if (
                manifest.input.version != pinned.version
                or manifest.input.dataset != binding.dataset
                or manifest.input_digest != pinned.input_digest
                or manifest.output_digest != pinned.output_digest
                or manifest.sha256 != pinned.manifest_sha256
                or digest(manifest.input.definition.preprocessing)
                != pinned.preprocessing_sha256
            ):
                raise ValueError("Frozen snapshot binding differs; no latest fallback")
            total_rows += len(item.rows)
            if total_rows > MAX_PROVIDER_ROWS:
                raise ValueError(
                    "In-memory provider row budget exceeded; use bounded storage projection"
                )
            for row in item.rows:
                total_bytes += len(canonical_bytes(row))
                if total_bytes > MAX_PROVIDER_BYTES:
                    raise ValueError("In-memory provider byte budget exceeded")
            validate_rows(manifest.input, item.rows)
            valid = sum(row.value_state == "valid" for row in item.rows)
            if (
                rows_digest(item.rows) != manifest.output_digest
                or len(item.rows) != manifest.row_count
                or valid != manifest.valid_count
                or len(item.rows) - valid != manifest.missing_count
            ):
                raise ValueError("Provider rows differ from admitted output identity")
            validate_history_inheritance(
                manifest.input.exposure_history, binding.exposure_history
            )
            selected[pinned.version.factor_version_id] = item
            grouped = {}
            for row in item.rows:
                grouped.setdefault(row.listing_id, []).append(row)
            indexed[pinned.version.factor_version_id] = MappingProxyType(
                {key: tuple(value) for key, value in grouped.items()}
            )
        self._binding = binding
        self._snapshots = MappingProxyType(selected)
        self._indexed = MappingProxyType(indexed)
        self._issued: dict[UUID, AccessReceipt] = {}

    @property
    def binding_sha256(self):
        return self._binding.sha256

    @property
    def receipts(self):
        return tuple(self._issued.values())

    def view(
        self,
        context: DecisionContext,
        requested_factor_ids: tuple[UUID, ...],
        listing_ids: tuple[str, ...] | None = None,
        history_sessions: int = 0,
    ) -> FeatureView:
        return self._project(
            context, requested_factor_ids, listing_ids, history_sessions, "provided"
        )

    def read_factor(
        self,
        context: DecisionContext,
        factor_version_id: UUID,
        listing_ids: tuple[str, ...] | None = None,
        history_sessions: int = 0,
    ) -> FeatureView:
        return self._project(
            context,
            (factor_version_id,),
            listing_ids,
            history_sessions,
            "read_requested",
        )

    def _project(self, context, factor_ids, listing_ids, history, level):
        if not isinstance(context, DecisionContext):
            raise ValueError("Only a host-owned decision context is accepted")  # noqa: TRY004
        if type(history) is not int or not 0 <= history <= 20:
            raise ValueError("Factor history exceeds bounded host projection")
        if not 1 <= len(factor_ids) <= 64 or len(set(factor_ids)) != len(factor_ids):
            raise ValueError("Requested factors must be bounded and unique")
        if not set(factor_ids) <= set(self._snapshots):
            raise ValueError("Factor is not part of the frozen bundle")
        listing_ids = context.listing_ids if listing_ids is None else tuple(listing_ids)
        if (
            not listing_ids
            or len(set(listing_ids)) != len(listing_ids)
            or not set(listing_ids) <= set(context.listing_ids)
        ):
            raise ValueError("Factor request exceeds host listing selection")
        selected, receipts, missing = [], [], []
        for factor_id in factor_ids:
            item = self._snapshots[factor_id]
            manifest = item.manifest
            fit = manifest.input.definition.preprocessing
            cutoff = context.information_cutoff
            rows = []
            for listing in listing_ids:
                if listing not in manifest.input.calculation_universe.listing_ids:
                    missing.append(
                        UnavailableValue(
                            factor_version_id=factor_id,
                            listing_id=listing,
                            reason="outside_calculation_universe",
                        )
                    )
                    continue
                available = [
                    row
                    for row in self._indexed[factor_id].get(listing, ())
                    if row.event_time <= cutoff
                    and row.available_at <= cutoff
                    and row.required_input_available_at <= cutoff
                    and row.membership_available_at <= cutoff
                    and row.lag_available_at <= cutoff
                    and (
                        not isinstance(fit, FittedPreprocessing)
                        or (fit.available_at <= cutoff and fit.last_input_at <= cutoff)
                    )
                ]
                dates = sorted({row.session_date for row in available})[
                    -(history + 1) :
                ]
                available = [row for row in available if row.session_date in dates]
                if not available:
                    missing.append(
                        UnavailableValue(
                            factor_version_id=factor_id,
                            listing_id=listing,
                            reason="not_available_at_cutoff",
                        )
                    )
                rows.extend(available)
            rows.sort(key=row_key)
            selected.extend(rows)
            payload = dict(  # noqa: C408 -- keyword names mirror immutable receipt contract
                level=level,
                decision_id=context.decision_id,
                event_seq=context.event_seq,
                stage_id=context.stage_id,
                information_cutoff=cutoff,
                run_binding_sha256=self.binding_sha256,
                factor_version_id=factor_id,
                snapshot_id=manifest.snapshot_id,
                output_digest=manifest.output_digest,
                listing_ids=listing_ids,
                history_sessions=history,
                rows_digest=rows_digest(tuple(rows)),
                row_count=len(rows),
                temporal_verification=item.verification.kind,
            )
            # Serialize datetimes/UUIDs through the typed model before deriving ID.
            provisional = AccessReceipt(receipt_id=UUID(int=0), **payload)
            receipt = AccessReceipt(
                receipt_id=uuid5(_RECEIPTS, digest(provisional)), **payload
            )
            receipts.append(receipt)
        if len(selected) > MAX_VIEW_ROWS:
            raise ValueError("Factor projection exceeds row budget")
        wire = {
            "rows": [r.model_dump(mode="json") for r in selected],
            "receipts": [r.model_dump(mode="json") for r in receipts],
            "unavailable": [r.model_dump(mode="json") for r in missing],
        }
        if len(canonical_bytes(wire)) > MAX_VIEW_BYTES:
            raise ValueError("Factor projection exceeds response byte budget")
        if len(set(self._issued) | {r.receipt_id for r in receipts}) > MAX_RECEIPTS:
            raise ValueError("Factor receipt budget exceeded")
        # Failed or oversized projections produce no false access receipts.
        self._issued.update((r.receipt_id, r) for r in receipts)
        return FeatureView(tuple(selected), tuple(receipts), tuple(missing))


class ConsumedStageReceipt(Contract):
    schema_version: Literal["mineagent.stage-consumption.v1"] = (
        "mineagent.stage-consumption.v1"
    )
    request_id: UUID
    decision_id: Identifier
    event_seq: Count
    stage: Literal["eligibility", "score", "allocation"]
    input_digest: Sha256
    output_digest: Sha256
    predecessor_output_digest: Sha256 | None
    access_receipt_ids: tuple[UUID, ...]
    source: Literal["host_observed"] = "host_observed"
    level: Literal["stage_consumed"] = "stage_consumed"
    causal_contribution: Literal["not_established"] = "not_established"


class StageController:
    """Small trusted consumer for the new protocol, not a trading/backtest engine.

    This proves stage output entered this controller's decision state. Only the
    later actual AKQuant integration can establish resulting orders or fills.
    """

    def __init__(self, context: DecisionContext, provider: FeatureProvider):
        self.context, self.provider = context, provider
        self.eligible = context.listing_ids
        self.scores = ()
        self.target_weights = ()
        self._next = "eligibility"
        self._pending: StageCall | None = None
        self._receipts: tuple[UUID, ...] = ()
        self._consumed: list[ConsumedStageReceipt] = []
        self._sequence = 0

    @property
    def consumed(self):
        return tuple(self._consumed)

    def begin(self, *, access_receipt_ids: tuple[UUID, ...] = ()) -> StageCall:
        if self._next is None or self._pending is not None:
            raise ValueError("Stage already pending or decision complete")
        issued = {receipt.receipt_id: receipt for receipt in self.provider.receipts}
        if len(set(access_receipt_ids)) != len(access_receipt_ids):
            raise ValueError("Duplicate access receipt")
        for receipt_id in access_receipt_ids:
            receipt = issued.get(receipt_id)
            if (
                receipt is None
                or receipt.decision_id != self.context.decision_id
                or receipt.event_seq != self.context.event_seq
                or receipt.information_cutoff != self.context.information_cutoff
                or receipt.stage_id != self._next
            ):
                raise ValueError("Only current host-issued receipts can bind a stage")
        stage = self._next
        values = {
            "binding": self.provider.binding_sha256,
            "decision": self.context.model_dump(mode="json"),
            "stage": stage,
            "eligible": self.eligible,
            "scores": [v.model_dump(mode="json") for v in self.scores],
            "receipts": [str(v) for v in access_receipt_ids],
        }
        input_digest = digest(values)
        self._sequence += 1
        call = StageCall(
            request_id=uuid5(_RECEIPTS, f"{input_digest}:{self._sequence}"),
            decision_id=self.context.decision_id,
            event_seq=self.context.event_seq,
            stage=stage,
            input_digest=input_digest,
            predecessor_output_digest=self._consumed[-1].output_digest
            if self._consumed
            else None,
            listing_ids=self.eligible,
        )
        self._pending, self._receipts = call, access_receipt_ids
        return call

    def discard(self):
        """Unconsumed output has no receipt and cannot become a causal edge."""
        self._pending, self._receipts = None, ()

    def consume(self, reply: StageReply) -> ConsumedStageReceipt:
        call = self._pending
        if call is None:
            raise ValueError("No current host stage request")
        validate_stage_reply(call, reply)
        if call.stage == "eligibility":
            self.eligible = tuple(
                v for v in call.listing_ids if v in reply.eligible_listing_ids
            )
            self._next = "score"
        elif call.stage == "score":
            self.scores = reply.values
            self._next = "allocation"
        else:
            self.target_weights = reply.values
            self._next = None
        receipt = ConsumedStageReceipt(
            request_id=call.request_id,
            decision_id=call.decision_id,
            event_seq=call.event_seq,
            stage=call.stage,
            input_digest=call.input_digest,
            output_digest=digest(reply),
            predecessor_output_digest=call.predecessor_output_digest,
            access_receipt_ids=self._receipts,
        )
        self._consumed.append(receipt)
        self._pending, self._receipts = None, ()
        return receipt
