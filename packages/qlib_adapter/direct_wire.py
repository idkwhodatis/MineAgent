"""Bounded direct-analysis wire format; integrity is not launch/result authority.

Only the isolated installed-Qlib entrypoint imports the native adapter. The host
may validate these primitives, but must separately resolve retained snapshot and
label objects and the protected, stopped process receipt.
"""

import math
from datetime import date
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from packages.factors.contracts import Contract, canonical_bytes, digest
from packages.qlib_adapter.contracts import (
    DirectProtocol,
    DirectResult,
    FactorPoint,
    LabelRow,
)

MAX_INPUT_BYTES = 1_048_576  # Matches inherited Contract metadata ceiling.
MAX_RESULT_BYTES = 1_048_576
MAX_ROWS = 10_000


class DirectInput(Contract):
    schema_version: Literal["mineagent.qlib-direct-input.v1"] = (
        "mineagent.qlib-direct-input.v1"
    )
    protocol: DirectProtocol
    factors: tuple[FactorPoint, ...] = Field(min_length=1, max_length=MAX_ROWS)
    labels: tuple[LabelRow, ...] = Field(min_length=1, max_length=MAX_ROWS)
    calendar_sessions: tuple[date, ...] = Field(min_length=2, max_length=8192)

    @model_validator(mode="after")
    def bindings(self):
        p = self.protocol
        if (
            digest([r.model_dump(mode="json") for r in self.factors])
            != p.factor_points_sha256
        ):
            raise ValueError("Factor projection differs from protocol")
        if (
            len(self.labels) != p.label_manifest.row_count
            or digest([r.model_dump(mode="json") for r in self.labels])
            != p.label_manifest.rows_sha256
        ):
            raise ValueError("Labels differ from manifest")
        sessions = self.calendar_sessions
        if (
            tuple(sorted(set(sessions))) != sessions
            or digest([d.isoformat() for d in sessions])
            != p.label_manifest.calendar_sessions_sha256
        ):
            raise ValueError("Calendar differs from manifest")
        keys = [(r.session, r.listing_id) for r in self.factors]
        if keys != sorted(set(keys)) or keys != [
            (r.session, r.listing_id) for r in self.labels
        ]:
            raise ValueError(
                "Factors and labels must align uniquely in canonical order"
            )
        positions = {d: i for i, d in enumerate(sessions)}
        zone = ZoneInfo(p.label_manifest.market_timezone)
        for factor, label in zip(self.factors, self.labels, strict=True):
            if (
                not p.window.start <= factor.session < p.window.end
                or factor.listing_id not in p.listing_ids
            ):
                raise ValueError("Observation is outside frozen window/universe")
            position = positions.get(label.session)
            horizon = p.label_manifest.horizon_sessions
            if (
                position is None
                or position + horizon >= len(sessions)
                or label.horizon_end != sessions[position + horizon]
                or label.horizon_end_time.astimezone(zone).date() != label.horizon_end
            ):
                raise ValueError("Forward label horizon differs from calendar")
        return self


def read_input(raw: bytes) -> DirectInput:
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_INPUT_BYTES:
        raise ValueError("Direct analysis input exceeds byte limit")
    result = DirectInput.model_validate_json(raw)
    if canonical_bytes(result) != raw:
        raise ValueError("Direct analysis input is not canonical")
    return result


def write_input(value: DirectInput) -> bytes:
    raw = canonical_bytes(value)
    read_input(raw)
    return raw


def read_result(raw: bytes, inputs: DirectInput) -> DirectResult:
    """Check result semantics against exact inputs, never recompute Qlib on host."""
    inputs = read_input(write_input(inputs))
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_RESULT_BYTES:
        raise ValueError("Direct analysis result exceeds byte limit")
    result = DirectResult.model_validate_json(raw)
    if (
        canonical_bytes(result) != raw
        or result.protocol_sha256 != inputs.protocol.sha256
    ):
        raise ValueError("Direct result canonical/protocol binding differs")
    expected = {}
    for factor, label in zip(inputs.factors, inputs.labels, strict=True):
        expected.setdefault(factor.session, [])
        if factor.value is not None and label.value is not None:
            expected[factor.session].append((factor.value, label.value))
    if [d.session for d in result.days] != list(expected):
        raise ValueError("Direct result omits or substitutes sessions")
    for day in result.days:
        pairs = expected[day.session]
        reason = (
            "insufficient_pairs"
            if len(pairs) < 2
            else "constant_cross_section"
            if len({x for x, _ in pairs}) < 2 or len({y for _, y in pairs}) < 2
            else "defined"
        )
        if day.pair_count != len(pairs) or day.reason != reason:
            raise ValueError("Direct result changes pairwise validity")
        if day.reason == "defined" and not all(
            math.isfinite(v) for v in (day.ic, day.rank_ic)
        ):
            raise ValueError("Direct result is nonfinite")
    return result
