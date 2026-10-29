"""027-only protected candidate rejection closure component.

Caller must select the exact installed successor and validate source/evaluation
failure semantics separately. This checks retained evidence, never launch/replay
permission, and never interprets absent successful output as candidate failure.
"""

import hashlib
from pathlib import PurePosixPath
from uuid import UUID

from backend.app.numeric_process_backup import SCOPE, validate_numeric_closure_unit
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.numeric_process_journal import (
    NumericIdentity,
    NumericLaunch,
    NumericStop,
)
from packages.research_runtime.numeric_rejection import (
    NumericRejection,
    validate_rejection_journal,
)
from packages.research_runtime.process_contracts import canonical, digest

SCHEMA = "mineagent.numeric-process-rejection.v1"


def manifest_keys(row):
    if (
        row.get("role") != "factor_cutoff_candidate"
        or row.get("profile") != "mineagent.factor-prefix-candidate.v2"
    ):
        raise ValueError("Rejection manifest requires candidate profile")
    for field in ("owner_id", "id"):
        if type(row[field]) is not str or str(UUID(row[field])) != row[field]:
            raise ValueError("Canonical manifest identity required")
    return {
        f"research-processes/{row['owner_id']}/{row['id']}/control/{name}.json"
        for name in ("launch", "identity", "stop", "rejection")
    }


def validate_rejection_closure_unit(
    row, indexes, physical, check, read_json, allowed, reservation_ids
):
    receipts = [
        value
        for value in indexes["research_process_receipts"].values()
        if value["admission_id"] == row["id"]
    ]
    kinds = {value["kind"]: value for value in receipts}
    if len(kinds) != len(receipts) or set(kinds) != {
        "launch_intent",
        "launch_identity",
        "stop",
        "rejection",
    }:
        raise ValueError("Exact rejection receipt inventory required")
    rejected = kinds["rejection"]
    if any(rejected[key] != row[key] for key in SCOPE) or rejected[
        "payload_sha256"
    ] != digest(rejected["payload"]):
        raise ValueError("Rejection receipt scope/hash differs")
    reservation = indexes["research_budget_reservations"].get(row["reservation_id"])
    settlements = [
        value
        for value in indexes["research_budget_settlements"].values()
        if value["reservation_id"] == row["reservation_id"]
    ]
    if (
        reservation is None
        or reservation["state"] != "settled"
        or len(settlements) != 1
    ):
        raise ValueError("Rejection requires one fully settled process hold")
    amount = BudgetVector.model_validate(reservation["amounts"])
    settled = settlements[0]
    if (
        settled["outcome"] != "conservative_unknown"
        or BudgetVector.model_validate(settled["charge"]) != amount
        or any(settled[key] != row[key] for key in SCOPE)
    ):
        raise ValueError("Rejection cannot refund its original process hold")
    # Reuse the unchanged historical three-record validator on its exact view.
    # No old grammar learns a new kind; the original indexes are not mutated.
    base = {
        **indexes,
        "research_process_receipts": {
            key: value
            for key, value in indexes["research_process_receipts"].items()
            if value is not rejected
        },
    }
    permitted, used = set(), set(reservation_ids)
    validate_numeric_closure_unit(
        row, base, physical, check, read_json, permitted, used
    )
    prefix = f"research-processes/{row['owner_id']}/{row['id']}/"
    records = {}
    for name, model in (
        ("launch", NumericLaunch),
        ("identity", NumericIdentity),
        ("stop", NumericStop),
        ("rejection", NumericRejection),
    ):
        key = prefix + "control/" + name + ".json"
        if key not in physical:
            raise ValueError("Protected rejection journal absent")
        raw = canonical(read_json(key))
        if len(raw) > 16384:
            raise ValueError("Protected rejection journal oversized")
        check(key, hashlib.sha256(raw).hexdigest(), len(raw))
        records[name] = model.model_validate_json(raw)
        permitted.add(key)
    launch, identity, stop, rejection = validate_rejection_journal(
        records["launch"], records["identity"], records["stop"], records["rejection"]
    )
    if rejection.model_dump(mode="json") != rejected["payload"]:
        raise ValueError("Physical rejection differs from persisted receipt")
    # Completed cleanup has no staged inputs. Exact bounded residuals are allowed
    # after a crash during cleanup; no other leaf/output/control file is admitted.
    for item in launch.intent.inputs.files:
        key = prefix + "inputs/" + PurePosixPath(item.destination).name
        if key in physical:
            check(key, item.sha256, item.size_bytes)
            permitted.add(key)
    if {key for key in physical if key.startswith(prefix)} - permitted:
        raise ValueError("Rejection cannot coexist with output or unknown files")
    allowed.update(permitted)
    reservation_ids.update(used)
    return launch, identity, stop, rejection
