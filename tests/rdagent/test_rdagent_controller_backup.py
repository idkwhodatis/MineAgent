"""Actual retained-byte tests with synthetic rows; no PG/cold-restore claim."""

import copy
import hashlib
from uuid import uuid4

import pytest
from test_rdagent_contracts import research_dict
from test_rdagent_controller_process import records

from backend.app import rdagent_controller_backup as module
from backend.app.rdagent_backup import FIELDS
from packages.factors.contracts import canonical_bytes
from packages.rdagent_adapter.contracts import FactorResearchConfig
from packages.rdagent_adapter.operational_bootstrap import RDOperationalBootstrap
from packages.rdagent_adapter.persisted_identity import LEGACY_ONLY_FIELDS
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.process_contracts import canonical, digest
from packages.research_runtime.process_persistence_codec import encode_row


@pytest.fixture
def retained(tmp_path):
    launch, identity, child, stop = records(tmp_path)
    a = launch.intent.admission
    configdata = research_dict()
    configdata["budget"]["storage_bytes"] = 200000000
    config = FactorResearchConfig.model_validate(configdata)
    specid = uuid4()
    bootstrap = RDOperationalBootstrap(
        owner_id=a.owner_id,
        run_id=a.run_id,
        attempt_id=a.attempt_id,
        generation=a.generation,
        spec_id=specid,
        attempt_binding_id=a.logical_operation_id,
        config=config,
        config_sha256=config.sha256,
        input_sha256=config.input.sha256,
    )
    a = a.model_copy(update={"input_sha256": bootstrap.sha256})
    intent = launch.intent.model_copy(
        update={
            "admission": a,
            "bootstrap_sha256": bootstrap.sha256,
            "bootstrap_size_bytes": len(canonical_bytes(bootstrap)),
        }
    )
    launch = launch.model_copy(update={"intent": intent})
    identity = identity.model_copy(
        update={
            "launch_sha256": digest(launch.model_dump(mode="json")),
            "admission_sha256": digest(a.model_dump(mode="json")),
        }
    )
    child = child.model_copy(
        update={
            "identity_sha256": digest(identity.model_dump(mode="json")),
            "admission_sha256": identity.admission_sha256,
        }
    )
    stop = stop.model_copy(
        update={
            "launch_sha256": identity.launch_sha256,
            "identity_sha256": child.identity_sha256,
            "child_sha256": digest(child.model_dump(mode="json")),
        }
    )
    row = {k: str(v) if hasattr(v, "hex") else v for k, v in encode_row(a).items()}
    row["created_at"] = "2026-10-26T00:00:00Z"
    scope = {k: row[k] for k in module.SCOPE}
    amounts = BudgetVector(
        process_slots=1, wall_milliseconds=60000, storage_bytes=module.STORAGE_FLOOR
    ).model_dump()
    reservation = {
        "id": row["reservation_id"],
        **scope,
        "purpose": "process_unit",
        "state": "settled",
        "amounts": amounts,
    }
    settlement = {
        "id": str(uuid4()),
        **scope,
        "reservation_id": reservation["id"],
        "charge": amounts.copy(),
        "outcome": "conservative_unknown",
    }
    payloads = {
        "launch_intent": intent,
        "launch_identity": identity,
        "controller_child": child,
        "stop": stop,
    }
    receipts = {}
    for kind, value in payloads.items():
        rid = str(uuid4())
        payload = value.model_dump(mode="json")
        receipts[rid] = {
            "id": rid,
            **scope,
            "admission_id": row["id"],
            "kind": kind,
            "payload": payload,
            "payload_sha256": digest(payload),
        }
    indexes = {
        "research_process_states": {
            row["id"]: {"admission_id": row["id"], **scope, "status": "stopped"}
        },
        "research_process_receipts": receipts,
        "research_budget_reservations": {reservation["id"]: reservation},
        "research_budget_settlements": {settlement["id"]: settlement},
    }
    run = {
        "id": row["run_id"],
        "owner_id": row["owner_id"],
        "engine": "rdagent",
        "purpose": "factor_research",
        "config": config.model_dump(mode="json"),
        "dataset_version_id": str(config.input.dataset.dataset_version_id),
        "request_sha256": config.sha256,
        "research_image_digest": a.image_id,
        "status": "failed",
        **dict.fromkeys(LEGACY_ONLY_FIELDS),
    }
    rd = {
        "schema_version": module.RD_SCHEMA,
        "tables": {name: [] for name in FIELDS},
        "external_bindings": {"runs": [run]},
    }
    rd["tables"]["factor_research_specs"] = [
        {
            "id": str(specid),
            "owner_id": row["owner_id"],
            "run_id": row["run_id"],
            "config": run["config"],
            "config_sha256": config.sha256,
            "input_sha256": config.input.sha256,
        }
    ]
    rd["tables"]["factor_research_attempt_bindings"] = [
        {
            "id": row["logical_operation_id"],
            **scope,
            "spec_id": str(specid),
            "resumed_checkpoint_id": None,
        }
    ]
    rd["tables"]["factor_controller_launches"] = [
        {
            "id": str(uuid4()),
            **scope,
            "admission_id": row["id"],
            "attempt_binding_id": row["logical_operation_id"],
            "bootstrap": bootstrap.model_dump(mode="json"),
            "bootstrap_sha256": bootstrap.sha256,
        }
    ]
    physical = {}
    prefix = f"research-processes/{row['owner_id']}/{row['id']}/control/"

    def write(name, value):
        key = prefix + name + ".json"
        raw = canonical(value.model_dump(mode="json"))
        path = tmp_path / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        physical[key] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
        }

    for name, value in [
        ("launch", launch),
        ("identity", identity),
        ("child", child),
        ("stop", stop),
    ]:
        write(name, value)

    def check(key, sha, size):
        assert physical[key] == {"sha256": sha, "size_bytes": size}

    def read(key, sha, limit):
        raw = (tmp_path / key).read_bytes()
        assert len(raw) <= limit
        return raw

    def validate(**changes):
        args = {
            "row": row,
            "indexes": indexes,
            "rd_source_closure": rd,
            "physical": physical,
            "check": check,
            "read_bytes": read,
            "allowed": set(),
            "reservation_ids": set(),
            "research_schema": module.SCHEMA,
        }
        args.update(changes)
        return module.validate_controller_closure_unit(**args)

    return {
        "row": row,
        "indexes": indexes,
        "rd": rd,
        "physical": physical,
        "root": tmp_path,
        "prefix": prefix,
        "validate": validate,
        "stop": stop,
        "write": write,
    }


def childless(f):
    receipts = f["indexes"]["research_process_receipts"]
    for key in list(receipts):
        if receipts[key]["kind"] == "controller_child":
            del receipts[key]
    stop = f["stop"].model_copy(
        update={"child_sha256": None, "child_kernel_identity_absent": None}
    )
    for receipt in receipts.values():
        if receipt["kind"] == "stop":
            receipt["payload"] = stop.model_dump(mode="json")
            receipt["payload_sha256"] = digest(receipt["payload"])
    key = f["prefix"] + "child.json"
    del f["physical"][key]
    (f["root"] / key).unlink()
    f["write"]("stop", stop)


def test_actual_retained_files_and_idempotent_read_only_validation(retained):
    assert retained["validate"]() is True
    assert retained["validate"]() is True
    assert len(module.manifest_keys(retained["row"])) == 4


def test_childless_failure_no_empty_child_file_manufactured(retained):
    childless(retained)
    assert retained["validate"]() is True
    assert (
        len(module.manifest_keys(retained["row"], physical=retained["physical"])) == 3
    )
    assert len(module.manifest_keys(retained["row"])) == 4
    assert not (retained["root"] / (retained["prefix"] + "child.json")).exists()


@pytest.mark.parametrize("status", ["succeeded", "paused", "interrupted", "running"])
def test_no_child_is_only_failed_or_cancelled(retained, status):
    childless(retained)
    retained["rd"]["external_bindings"]["runs"][0]["status"] = status
    with pytest.raises(ValueError, match="failure only"):
        retained["validate"]()


@pytest.mark.parametrize("table", module.NO_CHILD_TABLES)
def test_nochild_rejects_any_source_evaluation_closedcall_observation(retained, table):
    childless(retained)
    retained["rd"]["tables"][table].append(
        {k: retained["row"][k] for k in module.SCOPE}
    )
    with pytest.raises(ValueError, match="failure only"):
        retained["validate"]()


def test_failed_run_with_child_can_retain_source_for_separate_graph_validation(
    retained,
):
    # This unit checks controller provenance only, not this synthetic source row.
    retained["rd"]["tables"]["factor_generation_sources"].append(
        {k: retained["row"][k] for k in module.SCOPE}
    )
    assert retained["validate"]()


@pytest.mark.parametrize(
    "status", ["running", "uncertain", "launch_intended", "admitted"]
)
def test_unstopped_projection_rejected(retained, status):
    retained["indexes"]["research_process_states"][retained["row"]["id"]]["status"] = (
        status
    )
    with pytest.raises(ValueError, match="positively stopped"):
        retained["validate"]()


@pytest.mark.parametrize("name", ["launch", "identity", "child", "stop"])
def test_required_actual_file_absence_rejected(retained, name):
    del retained["physical"][retained["prefix"] + name + ".json"]
    with pytest.raises(ValueError, match="absent"):
        retained["validate"]()


def test_corrupted_actual_bytes_fail_even_when_metadata_matches(retained):
    path = retained["root"] / (retained["prefix"] + "child.json")
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="Actual protected"):
        retained["validate"]()


@pytest.mark.parametrize("mutation", ["measured", "partial", "missing", "scope"])
def test_conservative_settlement_exactness(retained, mutation):
    settlements = retained["indexes"]["research_budget_settlements"]
    row = next(iter(settlements.values()))
    if mutation == "measured":
        row["outcome"] = "measured"
    elif mutation == "partial":
        row["charge"]["storage_bytes"] -= 1
    elif mutation == "scope":
        row["owner_id"] = str(uuid4())
    else:
        settlements.clear()
    with pytest.raises(ValueError, match="conservative stopped"):
        retained["validate"]()


def test_held_budget_after_positive_stop_remains_conservative(retained):
    next(iter(retained["indexes"]["research_budget_reservations"].values()))[
        "state"
    ] = "uncertain"
    retained["indexes"]["research_budget_settlements"].clear()
    assert retained["validate"]()


def test_old_or_future_research_schema_not_inferred(retained):
    for version in ("v4", "v6"):
        with pytest.raises(ValueError, match="Explicit research-v5"):
            retained["validate"](
                research_schema="mineagent.research-restore-closure." + version
            )


def test_extra_active_ipc_or_staged_input_not_silently_backed_up(retained):
    retained["physical"][retained["prefix"].replace("control/", "ipc/ticket")] = {}
    with pytest.raises(ValueError, match="active IPC/staging"):
        retained["validate"]()


def test_binding_hash_and_foreign_owner_rejected(retained):
    original = copy.deepcopy(retained["rd"])
    retained["rd"]["tables"]["factor_controller_launches"][0]["bootstrap_sha256"] = (
        "f" * 64
    )
    with pytest.raises(ValueError, match="bootstrap/image"):
        retained["validate"]()
    retained["rd"].clear()
    retained["rd"].update(original)
    retained["rd"]["tables"]["factor_research_attempt_bindings"][0]["owner_id"] = str(
        uuid4()
    )
    with pytest.raises(ValueError, match="predecessor"):
        retained["validate"]()
