"""Cold evidence validation for registered analysis no-IPC rows, not resumption."""

import hashlib
from uuid import UUID

from packages.research_runtime.analysis_process_journal import (
    AnalysisExtraction,
    AnalysisIdentity,
    AnalysisLaunch,
    AnalysisStop,
    validate_analysis_journal,
)
from packages.research_runtime.process_contracts import canonical, digest
from packages.research_runtime.process_persistence_codec import decode_row

SCOPE = ("owner_id", "run_id", "attempt_id", "generation")


def validate_analysis_closure_unit(
    row,
    indexes,
    physical,
    check,
    read_json,
    allowed,
    reservation_ids,
):
    values = {key: value for key, value in row.items() if key != "created_at"}
    for field in (
        "id",
        "owner_id",
        "run_id",
        "attempt_id",
        "logical_operation_id",
        "reservation_id",
    ):
        values[field] = UUID(values[field])
    admission = decode_row(values)
    if (
        admission.role != "native_evaluator"
        or admission.profile != "mineagent.qlib-direct-analysis.v1"
        or admission.ipc.ipc_policy != "none"
    ):
        raise ValueError("Analysis admission profile differs")
    reservation = indexes["research_budget_reservations"].get(row["reservation_id"])
    if (
        reservation is None
        or reservation["purpose"] != "process_unit"
        or reservation["logical_key"]
        != "direct-analysis:" + row["logical_operation_id"]
        or any(reservation[k] != row[k] for k in SCOPE)
        or reservation["id"] in reservation_ids
        or reservation["amounts"]["evaluator_executions"] < 1
        or reservation["amounts"]["process_slots"] < 1
        or reservation["amounts"]["wall_milliseconds"] < 60000
        or reservation["amounts"]["storage_bytes"] < 67_174_400
    ):
        raise ValueError("Analysis reservation differs")
    reservation_ids.add(reservation["id"])
    state = indexes["research_process_states"][row["id"]]
    if state["status"] != "stopped" or any(state[k] != row[k] for k in SCOPE):
        raise ValueError("Analysis process not stopped")
    receipts = [
        r
        for r in indexes["research_process_receipts"].values()
        if r["admission_id"] == row["id"]
    ]
    by_kind = {r["kind"]: r for r in receipts}
    if len(by_kind) != len(receipts) or set(by_kind) not in (
        {"launch_intent", "launch_identity", "stop"},
        {"launch_intent", "launch_identity", "stop", "extraction"},
    ):
        raise ValueError("Analysis receipt set differs")
    for receipt in receipts:
        if any(receipt[k] != row[k] for k in SCOPE) or receipt[
            "payload_sha256"
        ] != digest(receipt["payload"]):
            raise ValueError("Analysis receipt scope/hash differs")
    prefix = f"research-processes/{row['owner_id']}/{row['id']}/"
    models = {
        "launch": AnalysisLaunch,
        "identity": AnalysisIdentity,
        "stop": AnalysisStop,
    }
    if "extraction" in by_kind:
        models["extraction"] = AnalysisExtraction
    records = {}
    for name, model in models.items():
        key = prefix + "control/" + name + ".json"
        if key not in physical:
            raise ValueError("Analysis protected record absent")
        data = canonical(read_json(key))
        if len(data) > 16384:
            raise ValueError("Analysis protected record oversized")
        check(key, hashlib.sha256(data).hexdigest(), len(data))
        allowed.add(key)
        records[name] = model.model_validate_json(data)
    launch, identity, stop, extraction = validate_analysis_journal(
        records["launch"],
        records["identity"],
        records["stop"],
        records.get("extraction"),
    )
    if launch.intent.admission != admission:
        raise ValueError("Analysis launch admission differs")
    expected = {
        "launch_intent": launch.intent,
        "launch_identity": identity,
        "stop": stop,
    }
    if extraction is not None and not stop.deadline_bound_met:
        raise ValueError("Overdue analysis stop cannot authorize extracted output")
    if extraction is not None:
        expected["extraction"] = extraction
        key = prefix + "output/" + extraction.basename
        if key not in physical:
            raise ValueError("Analysis output absent")
        check(key, extraction.sha256, extraction.size_bytes)
        allowed.add(key)
    if any(
        by_kind[k]["payload"] != value.model_dump(mode="json")
        for k, value in expected.items()
    ):
        raise ValueError("Analysis physical/DB evidence differs")
