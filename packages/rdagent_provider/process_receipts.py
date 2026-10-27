"""Bounded retained SDK evidence. Parsing historical evidence grants no authority."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.factors.contracts import Contract, Sha256, canonical_bytes

MAX_RECEIPT_BYTES = 16384
OBJECT_KIND = "rd_provider_process_receipt"


class SDKIdentity(Contract):
    pid: int = Field(strict=True, gt=0)
    start_ticks: int = Field(strict=True, gt=0)
    process_group: int = Field(strict=True, gt=0)
    session_id: int = Field(strict=True, gt=0)
    parent_pid: int = Field(strict=True, gt=0)
    boot_id: UUID
    deadline_monotonic_ns: int = Field(strict=True, gt=0)

    @model_validator(mode="after")
    def own_group(self):
        if self.pid != self.process_group or self.pid != self.session_id:
            raise ValueError("SDK must lead its exact process group and session")
        if self.parent_pid == self.pid:
            raise ValueError("SDK parent cannot be itself")
        return self


class ReceiptScope(Contract):
    schema_version: Literal["mineagent.rd-provider-process-receipt.v1"] = (
        "mineagent.rd-provider-process-receipt.v1"
    )
    owner_id: UUID
    run_id: UUID
    attempt_id: UUID
    generation: int = Field(strict=True, gt=0)
    transport_id: UUID
    request_sha256: Sha256
    model_binding_sha256: Sha256


class SDKIntent(ReceiptScope):
    kind: Literal["intent"] = "intent"
    admitted_monotonic_ns: int = Field(strict=True, gt=0)
    deadline_monotonic_ns: int = Field(strict=True, gt=0)
    maximum_duration_ms: int = Field(strict=True, gt=0, le=8000)
    boot_id: UUID

    @model_validator(mode="after")
    def bounded_deadline(self):
        duration = self.deadline_monotonic_ns - self.admitted_monotonic_ns
        if not 0 < duration <= self.maximum_duration_ms * 1_000_000:
            raise ValueError("SDK original deadline exceeds declared duration")
        return self


class SDKStarted(ReceiptScope):
    kind: Literal["started"] = "started"
    identity: SDKIdentity


class SDKStopped(ReceiptScope):
    kind: Literal["stopped"] = "stopped"
    identity: SDKIdentity
    observed_monotonic_ns: int = Field(strict=True, gt=0)
    leader_reaped: bool = Field(strict=True)
    process_group_absent: bool = Field(strict=True)

    @model_validator(mode="after")
    def positive_stop(self):
        if not self.leader_reaped or not self.process_group_absent:
            raise ValueError("Positive whole-group stop required")
        return self


def receipt_bytes(receipt):
    if type(receipt) not in (SDKIntent, SDKStarted, SDKStopped):
        raise ValueError("Unknown SDK receipt")
    raw = canonical_bytes(receipt)
    if len(raw) > MAX_RECEIPT_BYTES:
        raise ValueError("SDK receipt byte limit")
    return raw


def validate_chain(intent, started, stopped, *, completed):
    """Check original host observations; never query current /proc on restore.

    Late containment remains useful evidence but cannot authorize completion.
    Physical callbacks, retained bytes and DB scope must be checked by caller.
    """
    if (type(intent), type(started), type(stopped)) != (
        SDKIntent,
        SDKStarted,
        SDKStopped,
    ) or type(completed) is not bool:
        raise ValueError("Exact SDK chain types required")
    for row in (intent, started, stopped):
        receipt_bytes(row)
    fields = ReceiptScope.model_fields
    if any(
        getattr(intent, k) != getattr(row, k)
        for row in (started, stopped)
        for k in fields
    ):
        raise ValueError("SDK receipt scope drift")
    if started.identity != stopped.identity or (
        started.identity.boot_id != intent.boot_id
        or started.identity.deadline_monotonic_ns != intent.deadline_monotonic_ns
        or stopped.observed_monotonic_ns < intent.admitted_monotonic_ns
    ):
        raise ValueError("SDK receipt identity/deadline drift")
    if completed and stopped.observed_monotonic_ns > intent.deadline_monotonic_ns:
        raise ValueError("Late SDK cleanup cannot prove completed transport")
    return stopped


def parse_receipt(raw):
    from packages.strategy_sdk import JsonLimits, parse_json

    if type(raw) is not bytes:
        raise ValueError("Retained receipt bytes required")
    value = parse_json(
        raw, limits=JsonLimits(max_bytes=MAX_RECEIPT_BYTES, max_nodes=100, max_items=32)
    )
    classes = {"intent": SDKIntent, "started": SDKStarted, "stopped": SDKStopped}
    if type(value) is not dict or value.get("kind") not in classes:
        raise ValueError("Unknown SDK receipt kind")
    receipt = classes[value["kind"]].model_validate_json(raw)
    if receipt_bytes(receipt) != raw:
        raise ValueError("Noncanonical retained SDK receipt")
    return receipt
