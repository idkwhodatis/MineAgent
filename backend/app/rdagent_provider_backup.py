"""Historical SDK receipt reconstruction; never query or authorize a live process.

Every admitted provider transport requires positive whole-group stop evidence.
An intent-only or started-only transport blocks backup even for a failed run;
absence of a row is not evidence that an unobserved child never launched.
"""

from collections import Counter, defaultdict
from uuid import UUID

from packages.rdagent_provider.accounting import request_reservation
from packages.rdagent_provider.codec import PROTOCOLS, parse_request, request_sha256
from packages.rdagent_provider.object_identity import object_identity
from packages.rdagent_provider.process_receipts import (
    OBJECT_KIND,
    SDKIntent,
    parse_receipt,
    validate_chain,
)

SCOPE = ("owner_id", "run_id", "attempt_id", "generation")


def validate_receipts(
    rows,
    *,
    transports,
    operations,
    objects,
    configs,
    reservations,
    settlements,
    outcomes,
    read_bytes,
    workflow=False,
):
    if type(workflow) is not bool:
        raise ValueError("Explicit provider closure version required")
    grouped = {}
    charges_by_reservation, outcomes_by_transport = defaultdict(list), defaultdict(list)
    for row in settlements.values():
        charges_by_reservation[row["reservation_id"]].append(row)
    for row in outcomes.values():
        outcomes_by_transport[row["transport_id"]].append(row)
    for row in rows:
        transport = transports.get(row["transport_id"])
        if transport is None or any(row[k] != transport[k] for k in SCOPE):
            raise ValueError("Provider receipt transport scope differs")
        operation = operations.get(transport["operation_id"])
        if operation is None or operation.get("protocol") not in PROTOCOLS:
            raise ValueError("Provider receipt requires explicit provider protocol")
        if any(operation[k] != row[k] for k in SCOPE):
            raise ValueError("Provider operation scope differs")
        kind = row["kind"]
        members = grouped.setdefault(transport["id"], {})
        if kind not in {"intent", "started", "stopped"} or kind in members:
            raise ValueError("Provider receipt kind/uniqueness differs")
        obj = objects.get(row["receipt_object_id"])
        if (
            obj is None
            or obj["owner_id"] != row["owner_id"]
            or row["receipt_object_kind"] != OBJECT_KIND
            or obj["object_kind"] != OBJECT_KIND
            or obj["sha256"] != row["receipt_sha256"]
            or type(row["size_bytes"]) is not int
            or not 1 <= row["size_bytes"] <= 16384
            or obj["size_bytes"] != row["size_bytes"]
            or obj["storage_key"]
            != object_identity(
                UUID(row["owner_id"]), OBJECT_KIND, row["receipt_sha256"]
            ).storage_key
        ):
            raise ValueError("Provider receipt object differs")
        raw = read_bytes(obj["storage_key"], obj["sha256"], 16384)
        from hashlib import sha256

        if (
            type(raw) is not bytes
            or len(raw) != row["size_bytes"]
            or sha256(raw).hexdigest() != obj["sha256"]
        ):
            raise ValueError("Actual provider receipt bytes differ")
        document = parse_receipt(raw)
        if document.kind != kind or any(
            str(getattr(document, k)) != str(row[k]) for k in SCOPE
        ):
            raise ValueError("Provider receipt payload scope differs")
        config = configs[row["run_id"]]
        if getattr(config, "model_profile", None) != "configured.rdagent.v1":
            from packages.rdagent_adapter.workflow_config import (
                WorkflowFactorResearchConfig,
            )

            if not workflow or type(config) is not WorkflowFactorResearchConfig:
                raise ValueError("Provider receipt requires configured model identity")
        if (
            str(document.transport_id) != transport["id"]
            or document.request_sha256 != operation["request_sha256"]
            or document.model_binding_sha256 != config.model_binding.sha256
        ):
            raise ValueError("Provider receipt exact request/model differs")
        request = parse_request(operation["protocol"], operation["request"])
        if (
            request_sha256(operation["protocol"], request)
            != operation["request_sha256"]
        ):
            raise ValueError("Provider retained request commitment differs")
        request_reservation(request, config.model_binding)
        if (
            type(document) is SDKIntent
            and document.maximum_duration_ms != config.model_binding.request_deadline_ms
        ):
            raise ValueError("Provider original deadline differs from declared bound")
        members[kind] = document
    provider_ids = {
        row["id"]
        for row in transports.values()
        if operations[row["operation_id"]].get("protocol") in PROTOCOLS
    }
    if set(grouped) != provider_ids:
        raise ValueError("Provider transport lacks positive SDK evidence")
    for identifier, members in grouped.items():
        transport = transports[identifier]
        operation = operations[transport["operation_id"]]
        config = configs[operation["run_id"]]
        request = parse_request(operation["protocol"], operation["request"])
        expected = request_reservation(request, config.model_binding).model_dump()
        held = reservations.get(transport["reservation_id"])
        charges = charges_by_reservation[transport["reservation_id"]]
        results = outcomes_by_transport[identifier]
        if (
            held is None
            or held["state"] != "settled"
            or held["purpose"] != "model_transport"
            or held["amounts"] != expected
            or len(charges) != 1
            or len(results) != 1
            or charges[0]["outcome"] != "conservative_unknown"
            or charges[0]["charge"] != expected
            or results[0]["usage"] != expected
            or results[0]["outcome"] != transport["state"]
            or any(
                row[k] != transport[k]
                for row in (held, *charges, *results)
                for k in SCOPE
            )
        ):
            raise ValueError("Provider whole conservative charge differs")
        if set(members) != {"intent", "started", "stopped"}:
            raise ValueError("Partial SDK process evidence blocks backup")
        validate_chain(
            members["intent"],
            members["started"],
            members["stopped"],
            completed=transports[identifier]["state"] == "completed",
        )


def validate_operation_membership(operations, members, transports):
    """Every provider admission belongs to exactly one authenticated RD call."""
    admitted = [row for row in operations.values() if row.get("protocol") in PROTOCOLS]
    if any(count > 32 for count in Counter(row["run_id"] for row in admitted).values()):
        raise ValueError("Provider profile permits at most 32 operations per run")
    expected = {row["id"] for row in admitted}
    observed = set()
    for member in members:
        if member["protocol"] not in PROTOCOLS:
            continue
        operation = operations.get(member["operation_id"])
        if (
            operation is None
            or operation["id"] not in expected
            or operation["id"] in observed
            or operation["protocol"] != member["protocol"]
            or any(operation[k] != member[k] for k in SCOPE)
        ):
            raise ValueError("Provider exact call membership differs")
        observed.add(operation["id"])
    if expected != observed:
        raise ValueError("Provider operation has no unique authenticated call")

    counts = {key: 0 for key in expected}
    for transport in transports.values():
        key = transport["operation_id"]
        if key not in expected:
            continue
        if any(transport[k] != operations[key][k] for k in SCOPE):
            raise ValueError("Provider transport scope differs")
        counts[key] += 1
    if any(value != 1 for value in counts.values()):
        raise ValueError("Provider operation needs one atomic first transport")
