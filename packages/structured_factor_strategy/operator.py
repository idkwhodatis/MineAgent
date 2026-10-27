"""Trusted closed transform, never a getter-based claim about arbitrary Python.

No exec/import/callback hooks exist. Exact row reads feed the specified comparison,
sort and allocation directly. Runtime stop/image and persisted graph validation
remain separate requirements before records become actual execution evidence.
"""

from decimal import ROUND_DOWN, Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field

from packages.factors.contracts import Contract, Sha256, digest
from packages.strategy_sdk import (
    Observation,
    PortfolioReply,
    Score,
    Target,
    TargetPortfolio,
    hash_json,
)
from packages.structured_factor_strategy.contracts import (
    MAX_DECISIONS,
    StructuredEngineInput,
)


class RowRead(Contract):
    factor_listing_id: str
    native_listing_id: UUID
    snapshot_id: UUID
    factor_version_id: UUID
    row_sha256: Sha256 | None
    value: float | None
    availability: Literal[
        "available", "missing_row", "not_yet_available", "missing_value"
    ]


class DecisionRecord(Contract):
    event_seq: int = Field(strict=True, ge=0, lt=MAX_DECISIONS)
    observation: Observation
    observation_sha256: Sha256
    program_sha256: Sha256
    snapshot_id: UUID
    reads: tuple[RowRead, ...] = Field(max_length=100)
    eligible: tuple[UUID, ...] = Field(max_length=100)
    ranking: tuple[Score, ...] = Field(max_length=100)
    targets: tuple[Target, ...] = Field(max_length=100)
    eligibility_sha256: Sha256
    score_sha256: Sha256
    allocation_sha256: Sha256
    reply: PortfolioReply
    reply_sha256: Sha256
    qualification: Literal["closed_operator_transform_not_process_certificate"] = (
        "closed_operator_transform_not_process_certificate"
    )


def evaluate(inputs: StructuredEngineInput, observation: Observation, event_seq: int):
    """Exact deterministic transformation over the actual engine Observation."""
    if not 0 <= event_seq < MAX_DECISIONS:
        raise ValueError("Structured decision bound exceeded")
    program = inputs.program
    mapping = {
        str(item.native_listing_id): item.factor_listing_id
        for item in inputs.listing_bindings
    }
    native = {listing.listing_id for listing in observation.listings}
    if native != set(mapping):
        raise ValueError("Engine listing inventory differs from explicit factor map")
    reads = []
    eligible = []
    ranking = []
    targets = []
    active = (
        program.signal_window.start
        <= observation.session_date
        < program.signal_window.end
    )
    if observation.warmup:
        action = "no_decision"
    elif not active:
        action = "hold"
    else:
        by_listing = {
            row.listing_id: row
            for row in inputs.rows
            if row.session_date == observation.session_date
        }
        for key in sorted(native):
            factor_id = mapping[key]
            row = by_listing.get(factor_id)
            if row is None:
                availability = "missing_row"
            elif (
                max(
                    row.event_time,
                    row.available_at,
                    row.required_input_available_at,
                    row.membership_available_at,
                    row.lag_available_at,
                )
                > observation.decision_time
            ):
                availability = "not_yet_available"
            elif row.value_state != "valid":
                availability = "missing_value"
            else:
                availability = "available"
            value = row.value if availability == "available" else None
            reads.append(
                RowRead(
                    factor_listing_id=factor_id,
                    native_listing_id=UUID(key),
                    snapshot_id=inputs.snapshot.snapshot_id,
                    factor_version_id=inputs.snapshot.input.version.factor_version_id,
                    row_sha256=digest(row)
                    if row is not None and availability != "not_yet_available"
                    else None,
                    value=value,
                    availability=availability,
                )
            )
            if value is not None and value > program.threshold:
                eligible.append(UUID(key))
                ranking.append(Score(listing_id=key, value=value))
        ranking.sort(key=lambda item: (-item.value, item.listing_id))
        chosen = ranking[: program.top_k]
        if chosen:
            weight = min(
                Decimal(str(program.maximum_weight)),
                Decimal(str(program.maximum_gross_weight)) / len(chosen),
            ).quantize(Decimal("0.000000000001"), rounding=ROUND_DOWN)
            if weight <= 0:
                raise ValueError("Positive allocation lost at declared precision")
            targets = sorted(
                (
                    Target(listing_id=item.listing_id, value=float(weight))
                    for item in chosen
                ),
                key=lambda item: item.listing_id,
            )
            action = "rebalance"
        else:
            action = "liquidate"
    observation_sha = hash_json(observation)
    common = {
        "program_sha256": digest(program),
        "snapshot_id": str(inputs.snapshot.snapshot_id),
        "event_seq": event_seq,
        "observation_sha256": observation_sha,
    }
    eligibility_sha = digest(
        {
            **common,
            "stage": "eligibility",
            "reads": [r.model_dump(mode="json") for r in reads],
            "eligible": [str(x) for x in eligible],
        }
    )
    score_sha = digest(
        {
            **common,
            "stage": "score",
            "predecessor_sha256": eligibility_sha,
            "ranking": [r.model_dump(mode="json") for r in ranking],
        }
    )
    allocation_sha = digest(
        {
            **common,
            "stage": "allocation",
            "predecessor_sha256": score_sha,
            "targets": [t.model_dump(mode="json") for t in targets],
            "action": action,
        }
    )
    reply = PortfolioReply(
        decision=TargetPortfolio(
            signal_id=digest(
                {
                    "execution_id": str(inputs.execution_id),
                    "allocation_sha256": allocation_sha,
                }
            ),
            effective_at=observation.next_open_time,
            action=action,
            basis="target_weight",
            targets=tuple(targets),
            omitted_positions="preserve"
            if action in {"hold", "no_decision"}
            else "liquidate",
            scores=tuple(ranking),
        ),
        next_state=observation.state,
    )
    record = DecisionRecord(
        event_seq=event_seq,
        observation=observation,
        observation_sha256=observation_sha,
        program_sha256=digest(program),
        snapshot_id=inputs.snapshot.snapshot_id,
        reads=tuple(reads),
        eligible=tuple(eligible),
        ranking=tuple(ranking),
        targets=tuple(targets),
        eligibility_sha256=eligibility_sha,
        score_sha256=score_sha,
        allocation_sha256=allocation_sha,
        reply=reply,
        reply_sha256=hash_json(reply),
    )
    return record


class ClosedExchange:
    def __init__(self, inputs):
        self.inputs = inputs
        self.records = []

    def __call__(self, observation):
        if (
            self.records
            and observation.decision_time <= self.records[-1].observation.decision_time
        ):
            raise ValueError("Structured observations must be strictly ordered")
        record = evaluate(self.inputs, observation, len(self.records))
        self.records.append(record)
        return record.reply.model_dump(mode="json")
