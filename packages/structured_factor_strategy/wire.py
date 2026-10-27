"""Bounded explicit structured result; no portable policy/trust reclassification.

The legacy validator is reused only as an internal accounting-table validator.
Its temporary envelope never escapes this module and supplies NO execution grant.
A real protected process + persisted input/receipt graph is still mandatory.
"""

import hashlib
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from packages.factors.contracts import canonical_bytes, digest
from packages.strategy_sdk import JsonLimits, hash_json, parse_json, parse_reply
from packages.structured_factor_strategy.contracts import (
    MAX_DECISIONS,
    MAX_INPUT_BYTES,
    MAX_RESULT_BYTES,
    StructuredEngineInput,
)
from packages.structured_factor_strategy.operator import DecisionRecord, evaluate
from packages.tradegrad_adapter.portable_results import validate_portable_result

PROVENANCE = {
    "schema_version": "tradegrad.portable-result.v1",
    "evaluation_mode": "portable",
    "result_trust": "isolated_policy_trusted_akquant_engine",
    "ledger_origin": "trusted_engine",
    "runtime_certified": False,
}


class StructuredResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    schema_version: Literal["mineagent.structured-factor-engine-result.v1"] = (
        "mineagent.structured-factor-engine-result.v1"
    )
    profile: Literal["mineagent.factor-strategy-engine.v1"] = (
        "mineagent.factor-strategy-engine.v1"
    )
    result_trust: Literal["closed_operator_trusted_engine"] = (
        "closed_operator_trusted_engine"
    )
    execution_id: UUID
    structured_strategy_version_id: UUID
    snapshot_id: UUID
    strategy_input_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    market_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    decisions: tuple[DecisionRecord, ...] = Field(
        min_length=1, max_length=MAX_DECISIONS
    )
    accounting_tables: dict
    qualification: Literal[
        "structured_transform_and_native_accounting_not_financial_gain_or_generic_causality"
    ] = "structured_transform_and_native_accounting_not_financial_gain_or_generic_causality"


def write_input(value):
    value = StructuredEngineInput.model_validate_json(canonical_bytes(value))
    raw = canonical_bytes(value)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("Structured input too large")
    return raw


def read_input(raw):
    parsed = parse_json(
        raw,
        limits=JsonLimits(max_bytes=MAX_INPUT_BYTES, max_nodes=100000, max_items=10000),
    )
    value = StructuredEngineInput.model_validate_json(canonical_bytes(parsed))
    if write_input(value) != raw:
        raise ValueError("Structured input is not exact canonical bytes")
    return value


def accounting_only(native):
    validate_portable_result(native)
    if any(native.get(k) != v for k, v in PROVENANCE.items()):
        raise ValueError("Unexpected engine export")
    result = {k: v for k, v in native.items() if k not in PROVENANCE}
    result["adapter_manifest"] = result.pop("manifest")
    return result


def validate_accounting(tables):
    # Adapter-only closed grammar reuse, NOT provenance evidence or persisted old ABI.
    if (
        type(tables) is not dict
        or set(tables) & set(PROVENANCE)
        or "manifest" in tables
        or "adapter_manifest" not in tables
    ):
        raise ValueError("Structured accounting cannot carry legacy trust envelope")
    temporary = {**tables, **PROVENANCE, "manifest": tables["adapter_manifest"]}
    del temporary["adapter_manifest"]
    return validate_portable_result(temporary)


def read_result(raw, inputs):
    parsed = parse_json(
        raw,
        limits=JsonLimits(
            max_bytes=MAX_RESULT_BYTES, max_nodes=1000000, max_items=100000
        ),
    )
    result = StructuredResult.model_validate_json(canonical_bytes(parsed))
    if canonical_bytes(result) != raw:
        raise ValueError("Structured result is not exact canonical bytes")
    if (
        result.execution_id,
        result.structured_strategy_version_id,
        result.snapshot_id,
        result.strategy_input_sha256,
        result.market_sha256,
    ) != (
        inputs.execution_id,
        inputs.structured_strategy_version_id,
        inputs.snapshot.snapshot_id,
        hashlib.sha256(write_input(inputs)).hexdigest(),
        inputs.market_sha256,
    ):
        raise ValueError("Structured output input identity differs")
    tables = validate_accounting(result.accounting_tables)
    if tables["manifest"] != inputs.engine.manifest.model_dump(mode="json") or tables[
        "window"
    ] != {**inputs.engine.window.model_dump(mode="json"), "interval": "half_open"}:
        raise ValueError("Native adapter/window differs")
    expected_identity = {
        "strategy_sha256": digest(inputs.program),
        "parameters_sha256": digest(inputs.program),
        "manifest_sha256": hash_json(inputs.engine.manifest),
        **inputs.engine.input_identity.model_dump(mode="json"),
    }
    if tables["identity"] != expected_identity:
        raise ValueError("Native accounting input identity differs")
    trace = tables["decision_trace"]
    if len(trace) != len(result.decisions):
        raise ValueError("Stage/native decision inventory differs")
    logical = {row["logical_order_id"]: row for row in tables["logical_orders"]}
    previous = None
    from runtimes.portable_engine.runtime import target_orders

    for seq, (record, native) in enumerate(zip(result.decisions, trace, strict=True)):
        if record.event_seq != seq or (
            previous is not None and record.observation.decision_time <= previous
        ):
            raise ValueError("Stage decision ordering differs")
        previous = record.observation.decision_time
        recomputed = evaluate(inputs, record.observation, seq)
        if canonical_bytes(recomputed) != canonical_bytes(record):
            raise ValueError("Closed transformation differs")
        parse_reply(
            canonical_bytes(record.reply), inputs.engine.manifest, record.observation
        )
        if (
            native["sequence"],
            native["observation_sha256"],
            native["reply_sha256"],
            native["decision"],
        ) != (
            seq,
            record.observation_sha256,
            record.reply_sha256,
            record.reply.decision.model_dump(mode="json"),
        ):
            raise ValueError("Closed stages do not bind actual engine decision")
        plans = target_orders(
            record.reply.decision, record.observation, tables["engine"]["profile"]
        )
        skipped = record.observation.warmup or (
            record.observation.open_orders
            and record.reply.decision.action not in {"hold", "no_decision"}
        )
        if skipped:
            plans = []
        actual = [
            (logical[key]["listing_id"], logical[key]["side"], logical[key]["quantity"])
            for key in native["logical_order_ids"]
        ]
        if actual != plans:
            raise ValueError("Actual logical orders differ from trusted target sizing")
    return result
