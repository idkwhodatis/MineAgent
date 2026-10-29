"""Distinct027 SDK receipt wire, sharing only reviewed physical field validators.

Neither RD receipt bytes nor a decoded DTO can authorize an OE operation.
The host must bind immutable membership, request, live peer and charged files.
"""

from typing import Literal

from packages.factors.contracts import canonical_bytes
from packages.rdagent_provider.process_receipts import (
    MAX_RECEIPT_BYTES,
)
from packages.rdagent_provider.process_receipts import (
    SDKIntent as PhysicalIntent,
)
from packages.rdagent_provider.process_receipts import (
    SDKStarted as PhysicalStarted,
)
from packages.rdagent_provider.process_receipts import (
    SDKStopped as PhysicalStopped,
)
from packages.strategy_sdk import JsonLimits, parse_json

OBJECT_KIND = "oe_structured_provider_receipt"
SCHEMA = "mineagent.oe-structured-provider-process-receipt.v1"


class SDKIntent(PhysicalIntent):
    schema_version: Literal["mineagent.oe-structured-provider-process-receipt.v1"] = (
        SCHEMA
    )


class SDKStarted(PhysicalStarted):
    schema_version: Literal["mineagent.oe-structured-provider-process-receipt.v1"] = (
        SCHEMA
    )


class SDKStopped(PhysicalStopped):
    schema_version: Literal["mineagent.oe-structured-provider-process-receipt.v1"] = (
        SCHEMA
    )


CLASSES = {"intent": SDKIntent, "started": SDKStarted, "stopped": SDKStopped}


def receipt_bytes(receipt):
    if type(receipt) not in CLASSES.values():
        raise ValueError("Exact structured OE provider receipt required")
    raw = canonical_bytes(receipt)
    # Reparse strict JSON to reject forged model_copy/object attribute changes.
    checked = type(receipt).model_validate_json(raw)
    if checked != receipt or len(raw) > MAX_RECEIPT_BYTES:
        raise ValueError("Provider receipt grammar or size drift")
    return raw


def parse_receipt(raw):
    if type(raw) is not bytes:
        raise ValueError("Retained original receipt bytes required")
    value = parse_json(
        raw, limits=JsonLimits(max_bytes=MAX_RECEIPT_BYTES, max_nodes=100, max_items=32)
    )
    if type(value) is not dict or value.get("kind") not in CLASSES:
        raise ValueError("Unknown provider receipt kind")
    receipt = CLASSES[value["kind"]].model_validate_json(raw)
    if receipt_bytes(receipt) != raw:
        raise ValueError("Noncanonical provider receipt")
    return receipt


def validate_chain(intent, started, stopped, *, completed):
    if (type(intent), type(started), type(stopped)) != (
        SDKIntent,
        SDKStarted,
        SDKStopped,
    ) or type(completed) is not bool:
        raise ValueError("Exact structured OE physical receipt chain required")
    for row in (intent, started, stopped):
        receipt_bytes(row)
    fields = (
        "schema_version",
        "owner_id",
        "run_id",
        "attempt_id",
        "generation",
        "transport_id",
        "request_sha256",
        "model_binding_sha256",
    )
    if any(
        getattr(intent, k) != getattr(row, k)
        for row in (started, stopped)
        for k in fields
    ):
        raise ValueError("Provider receipt scope/request drift")
    if started.identity != stopped.identity or (
        started.identity.boot_id != intent.boot_id
        or started.identity.deadline_monotonic_ns != intent.deadline_monotonic_ns
        or stopped.observed_monotonic_ns < intent.admitted_monotonic_ns
    ):
        raise ValueError("Provider original identity/deadline drift")
    if completed and stopped.observed_monotonic_ns > intent.deadline_monotonic_ns:
        raise ValueError("Late containment is not completed transport")
    return stopped
