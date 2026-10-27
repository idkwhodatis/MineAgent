"""024 cold process evidence, never a new launch or resume authority.

Both profiles require exact retained physical bytes, immutable DB receipts,
positive containment and the full conservative resource charge. Domain consumers
must separately bind bootstrap/input/output identities to their search graph.
"""

import hashlib
from uuid import UUID

from packages.research_runtime.process_contracts import canonical, digest
from packages.research_runtime.process_persistence_codec import decode_row
from runtimes.openevolve_evaluator import journal as native
from runtimes.openevolve_evaluator.contracts import PROFILE as NATIVE_PROFILE
from runtimes.openevolve_operational import process_journal as controller
from runtimes.openevolve_operational.profile import STORAGE_FLOOR

SCOPE = ("owner_id", "run_id", "attempt_id", "generation")


def validate_process_unit(
    row, indexes, physical, check, read_bytes, allowed, reservation_ids
):
    if not callable(read_bytes):
        raise TypeError("OE cold closure requires actual bounded bytes")
    values = {k: v for k, v in row.items() if k != "created_at"}
    for key in (
        "id",
        "owner_id",
        "run_id",
        "attempt_id",
        "logical_operation_id",
        "reservation_id",
    ):
        values[key] = UUID(values[key])
    admission = decode_row(values)
    is_controller = admission.profile == controller.PROFILE
    if not is_controller and admission.profile != NATIVE_PROFILE:
        raise ValueError("Unregistered OE process profile")
    if is_controller:
        controller.controller_admission(admission)
        minimum = {
            "process_slots": 1,
            "wall_milliseconds": 600000,
            "storage_bytes": STORAGE_FLOOR,
        }
        logical_key = "oe-controller:" + row["logical_operation_id"]
        models = {
            "launch": controller.ControllerLaunch,
            "identity": controller.ControllerIdentity,
            "stop": controller.ControllerStop,
        }
        optional = ("controller_child", "child", controller.ControllerChild)
    else:
        minimum = {
            "process_slots": 1,
            "evaluator_executions": 1,
            "wall_milliseconds": 90000,
            "storage_bytes": 268500992,
        }
        logical_key = f"oe-native:{row['logical_operation_id']}:{row['launch_ordinal']}"
        models = {
            "launch": native.NativeLaunch,
            "identity": native.NativeIdentity,
            "stop": native.NativeStop,
        }
        optional = ("extraction", "extraction", native.NativeExtraction)
    reservation = indexes["research_budget_reservations"].get(row["reservation_id"])
    if (
        reservation is None
        or reservation["purpose"] != "process_unit"
        or reservation["logical_key"] != logical_key
        or reservation["state"] != "settled"
        or any(reservation[k] != row[k] for k in SCOPE)
        or reservation["id"] in reservation_ids
        or any(
            type(reservation["amounts"].get(k)) is not int
            or reservation["amounts"][k] < v
            for k, v in minimum.items()
        )
    ):
        raise ValueError("OE exact settled process reservation required")
    settlements = [
        s
        for s in indexes["research_budget_settlements"].values()
        if s["reservation_id"] == reservation["id"]
    ]
    if (
        len(settlements) != 1
        or any(settlements[0][k] != row[k] for k in SCOPE)
        or settlements[0]["charge"] != reservation["amounts"]
        or settlements[0]["outcome"] != "conservative_unknown"
    ):
        raise ValueError("OE full conservative process charge required")
    state = indexes["research_process_states"].get(row["id"])
    if (
        state is None
        or state["status"] != "stopped"
        or any(state[k] != row[k] for k in SCOPE)
    ):
        raise ValueError("OE process lacks positive stopped projection")
    receipts = [
        r
        for r in indexes["research_process_receipts"].values()
        if r["admission_id"] == row["id"]
    ]
    by_kind = {r["kind"]: r for r in receipts}
    required = {"launch_intent", "launch_identity", "stop"}
    if len(by_kind) != len(receipts) or set(by_kind) not in (
        required,
        required | {optional[0]},
    ):
        raise ValueError("OE exact receipt inventory required")
    for receipt in receipts:
        if any(receipt[k] != row[k] for k in SCOPE) or receipt[
            "payload_sha256"
        ] != digest(receipt["payload"]):
            raise ValueError("OE process receipt scope/hash differs")
    if optional[0] in by_kind:
        models[optional[1]] = optional[2]
    prefix = f"research-processes/{row['owner_id']}/{row['id']}/"
    records, paths = {}, set()
    for name, model in models.items():
        key = prefix + "control/" + name + ".json"
        if key not in physical:
            raise ValueError("OE protected process record absent")
        raw = read_bytes(key, physical[key]["sha256"], 16384)
        if type(raw) is not bytes or not 0 < len(raw) <= 16384:
            raise ValueError("OE protected process record exceeds bounds")
        value = model.model_validate_json(raw)
        if canonical(value.model_dump(mode="json")) != raw:
            raise ValueError("OE process record is not exact canonical bytes")
        check(key, hashlib.sha256(raw).hexdigest(), len(raw))
        records[name] = value
        paths.add(key)
    launch, identity, stop = (records[k] for k in ("launch", "identity", "stop"))
    if launch.intent.admission != admission:
        raise ValueError("OE physical launch admission differs")
    expected = {
        "launch_intent": launch.intent,
        "launch_identity": identity,
        "stop": stop,
    }
    if is_controller:
        child = records.get("child")
        controller.validate_records(launch, identity, child, stop)
        if child is not None:
            expected["controller_child"] = child
    else:
        extraction = records.get("extraction")
        native.validate_native_journal(launch, identity, stop, extraction)
        if extraction is not None:
            from runtimes.openevolve_evaluator.protected_output import (
                MAX_FRAME,
                validate_export,
            )

            key = prefix + "output/" + extraction.basename
            if key not in physical:
                raise ValueError("OE native export absent")
            raw = read_bytes(key, extraction.sha256, MAX_FRAME)
            if (
                type(raw) is not bytes
                or len(raw) != extraction.size_bytes
                or hashlib.sha256(raw).hexdigest() != extraction.sha256
            ):
                raise ValueError("OE native export identity differs")
            validate_export(raw)
            check(key, extraction.sha256, extraction.size_bytes)
            paths.add(key)
            expected["extraction"] = extraction
    if any(
        by_kind[k]["payload"] != value.model_dump(mode="json")
        for k, value in expected.items()
    ):
        raise ValueError("OE physical/DB receipt disagreement")
    # Only a fully validated unit contributes accepted paths or consumes its hold.
    reservation_ids.add(reservation["id"])
    allowed.update(paths)
    return records
