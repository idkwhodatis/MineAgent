"""Cold byte/receipt consistency only: fixtures are not actual Docker evidence."""

import hashlib
from copy import deepcopy
from uuid import UUID, uuid4

import pytest

from backend.app.openevolve_process_backup import SCOPE, validate_process_unit
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.process_contracts import canonical, digest
from packages.research_runtime.process_persistence_codec import encode_row
from runtimes.openevolve_operational.profile import STORAGE_FLOOR
from tests.openevolve.operational_fixtures import records as controller_records
from tests.openevolve.test_native_evaluator_journal import records as native_records


def fixture(tmp_path, profile):
    if profile == "controller":
        launch, identity, child, stop = controller_records(tmp_path)
        models = {"launch": launch, "identity": identity, "child": child, "stop": stop}
        payloads = {
            "launch_intent": launch.intent,
            "launch_identity": identity,
            "controller_child": child,
            "stop": stop,
        }
        budget = BudgetVector(
            process_slots=1, wall_milliseconds=600000, storage_bytes=STORAGE_FLOOR
        )
        key = "oe-controller:" + str(launch.intent.admission.logical_operation_id)
    else:
        launch, identity, stop, _ = native_records()
        models = {"launch": launch, "identity": identity, "stop": stop}
        payloads = {
            "launch_intent": launch.intent,
            "launch_identity": identity,
            "stop": stop,
        }
        budget = BudgetVector(
            process_slots=1,
            evaluator_executions=1,
            wall_milliseconds=90000,
            storage_bytes=268500992,
        )
        key = f"oe-native:{launch.intent.admission.logical_operation_id}:1"
    row = {
        k: str(v) if isinstance(v, UUID) else v
        for k, v in encode_row(launch.intent.admission).items()
    }
    scope = {k: row[k] for k in SCOPE}
    reservation = dict(
        scope,
        id=row["reservation_id"],
        purpose="process_unit",
        logical_key=key,
        state="settled",
        amounts=budget.model_dump(),
    )
    settlement = dict(
        scope,
        id=str(uuid4()),
        reservation_id=reservation["id"],
        charge=budget.model_dump(),
        outcome="conservative_unknown",
    )
    receipts = {}
    for kind, payload in payloads.items():
        rid = str(uuid4())
        value = payload.model_dump(mode="json")
        receipts[rid] = dict(
            scope,
            id=rid,
            admission_id=row["id"],
            kind=kind,
            payload=value,
            payload_sha256=digest(value),
        )
    indexes = {
        "research_budget_reservations": {reservation["id"]: reservation},
        "research_budget_settlements": {settlement["id"]: settlement},
        "research_process_states": {row["id"]: dict(scope, status="stopped")},
        "research_process_receipts": receipts,
    }
    prefix = f"research-processes/{row['owner_id']}/{row['id']}/control/"
    files = {
        prefix + name + ".json": canonical(value.model_dump(mode="json"))
        for name, value in models.items()
    }
    physical = {
        k: {
            "path": k,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
        }
        for k, raw in files.items()
    }
    return row, indexes, physical, files


def run(case):
    row, indexes, physical, files = case

    def read(key, sha, cap):
        raw = files[key]
        assert len(raw) <= cap and hashlib.sha256(raw).hexdigest() == sha
        return raw

    def check(key, sha, size):
        assert physical[key]["sha256"] == sha and physical[key]["size_bytes"] == size

    allowed, holds = set(), set()
    result = validate_process_unit(row, indexes, physical, check, read, allowed, holds)
    assert allowed == set(files)
    assert holds == {row["reservation_id"]}
    return result


@pytest.mark.parametrize("profile", ["controller", "native"])
def test_exact_stopped_full_charge_actual_bytes(tmp_path, profile):
    assert "stop" in run(fixture(tmp_path, profile))


@pytest.mark.parametrize("profile", ["controller", "native"])
@pytest.mark.parametrize(
    "damage",
    [
        "pending",
        "refund",
        "outcome",
        "scope",
        "duplicate_settlement",
        "missing_stop",
        "unknown_receipt",
        "missing_file",
        "payload",
    ],
)
def test_cold_barriers(tmp_path, profile, damage):
    case = fixture(tmp_path, profile)
    row, indexes, physical, _files = case
    reservation = indexes["research_budget_reservations"][row["reservation_id"]]
    settlement = next(iter(indexes["research_budget_settlements"].values()))
    if damage == "pending":
        reservation["state"] = "held"
    elif damage == "refund":
        settlement["charge"] = {**settlement["charge"], "process_slots": 0}
    elif damage == "outcome":
        settlement["outcome"] = "measured"
    elif damage == "scope":
        settlement["run_id"] = str(uuid4())
    elif damage == "duplicate_settlement":
        indexes["research_budget_settlements"][str(uuid4())] = deepcopy(settlement)
    elif damage == "missing_stop":
        indexes["research_process_states"][row["id"]]["status"] = "unknown"
    elif damage == "unknown_receipt":
        receipt = deepcopy(next(iter(indexes["research_process_receipts"].values())))
        receipt["kind"] = "unknown"
        indexes["research_process_receipts"][str(uuid4())] = receipt
    elif damage == "missing_file":
        physical.pop(next(iter(physical)))
    elif damage == "payload":
        receipt = next(iter(indexes["research_process_receipts"].values()))
        receipt["payload_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        run(case)


def test_noncanonical_real_journal_rejected_even_with_inventory_hash(tmp_path):
    case = fixture(tmp_path, "controller")
    _, _, physical, files = case
    key = next(iter(files))
    files[key] += b"\n"
    physical[key] = {
        "path": key,
        "sha256": hashlib.sha256(files[key]).hexdigest(),
        "size_bytes": len(files[key]),
    }
    with pytest.raises(ValueError, match="canonical"):
        run(case)


def test_native_extracted_bytes_reparsed_not_just_hash(tmp_path):
    from runtimes.openevolve_evaluator.journal import NativeExtraction
    from runtimes.openevolve_evaluator.protected_output import export_payload

    case = fixture(tmp_path, "native")
    row, indexes, physical, files = case
    receipts = indexes["research_process_receipts"]
    by_kind = {r["kind"]: r for r in receipts.values()}
    payload = export_payload(
        {"metrics.json": b"{}", "stdout.bin": b"native observed", "stderr.bin": b""}, 0
    )
    extraction = NativeExtraction(
        launch_sha256=by_kind["launch_identity"]["payload"]["launch_sha256"],
        identity_sha256=by_kind["stop"]["payload"]["identity_sha256"],
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        manifest_sha256=by_kind["launch_intent"]["payload"]["inputs"][
            "manifest_sha256"
        ],
        native_artifacts_sha256=by_kind["launch_intent"]["payload"]["inputs"][
            "native_artifacts_sha256"
        ],
        protected_frame_complete=True,
        producer_quiesced=True,
    )
    value = extraction.model_dump(mode="json")
    rid = str(uuid4())
    receipts[rid] = {
        **{k: row[k] for k in SCOPE},
        "id": rid,
        "admission_id": row["id"],
        "kind": "extraction",
        "payload": value,
        "payload_sha256": digest(value),
    }
    prefix = f"research-processes/{row['owner_id']}/{row['id']}/"
    files[prefix + "control/extraction.json"] = canonical(value)
    files[prefix + "output/native-export.bin"] = payload
    for key, raw in files.items():
        physical[key] = {
            "path": key,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
        }
    assert "extraction" in run(case)
    # A fresh matching DB hash cannot make arbitrary output a protected export.
    payload = b"invalid export"
    value.update(sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload))
    receipts[rid]["payload_sha256"] = digest(value)
    files[prefix + "control/extraction.json"] = canonical(value)
    files[prefix + "output/native-export.bin"] = payload
    for key, raw in files.items():
        physical[key] = {
            "path": key,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
        }
    with pytest.raises(ValueError):
        run(case)
