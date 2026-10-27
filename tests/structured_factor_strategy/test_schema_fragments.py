"""Pure SQL generation/cold evidence tests; no PostgreSQL/runtime execution."""

import hashlib
from pathlib import Path

import pytest

from backend.app.structured_process_backup import validate_structured_closure_unit
from backend.app.structured_process_ddl import render_sql
from packages.research_runtime.process_contracts import canonical, digest
from packages.research_runtime.process_persistence_codec import encode_row
from tests.structured_factor_strategy.test_process_journal import records


def closure():
    launch, identity, stop, extraction = records()
    raw = b'{"opaque":true}'
    extraction = extraction.model_copy(
        update={"size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    )
    a = launch.intent.admission
    row = {k: str(v) if hasattr(v, "hex") else v for k, v in encode_row(a).items()}
    scope = {k: row[k] for k in ("owner_id", "run_id", "attempt_id", "generation")}
    reservation = {
        **scope,
        "id": row["reservation_id"],
        "purpose": "process_unit",
        "state": "settled",
        "logical_key": "structured-factor-execution:" + row["logical_operation_id"],
        "amounts": {
            "evaluator_executions": 1,
            "process_slots": 1,
            "wall_milliseconds": 90000,
            "storage_bytes": 268500992,
        },
    }
    indexes = {
        "research_budget_reservations": {reservation["id"]: reservation},
        "research_budget_settlements": {
            "settled": {
                **scope,
                "reservation_id": reservation["id"],
                "charge": dict(reservation["amounts"]),
                "outcome": "conservative_unknown",
            }
        },
        "research_process_states": {row["id"]: {**scope, "status": "stopped"}},
        "research_process_receipts": {},
    }
    for kind, value in (
        ("launch_intent", launch.intent),
        ("launch_identity", identity),
        ("stop", stop),
        ("extraction", extraction),
    ):
        payload = value.model_dump(mode="json")
        indexes["research_process_receipts"][kind] = {
            **scope,
            "admission_id": row["id"],
            "kind": kind,
            "payload": payload,
            "payload_sha256": digest(payload),
        }
    prefix = f"research-processes/{a.owner_id}/{a.id}/"
    physical = {
        prefix + "control/" + name + ".json": canonical(value.model_dump(mode="json"))
        for name, value in (
            ("launch", launch),
            ("identity", identity),
            ("stop", stop),
            ("extraction", extraction),
        )
    }
    physical[prefix + "output/result.json"] = raw
    return row, indexes, physical


def validate(row, indexes, physical):
    import json

    def check(key, sha, size):
        assert (
            len(physical[key]) == size
            and hashlib.sha256(physical[key]).hexdigest() == sha
        )

    allowed = set()
    validate_structured_closure_unit(
        row, indexes, physical, check, lambda k: json.loads(physical[k]), allowed, set()
    )
    assert allowed == set(physical)


def test_cold_stopped_chain_preserves_original_root_identity():
    validate(*closure())


@pytest.mark.parametrize("mutation", ["floor", "logical_key", "payload", "missing"])
def test_cold_graph_rejects_changed_evidence(mutation):
    row, indexes, physical = closure()
    if mutation == "floor":
        indexes["research_budget_reservations"][row["reservation_id"]]["amounts"][
            "wall_milliseconds"
        ] = 60000
    if mutation == "logical_key":
        indexes["research_budget_reservations"][row["reservation_id"]][
            "logical_key"
        ] = "direct-analysis:" + row["logical_operation_id"]
    if mutation == "payload":
        indexes["research_process_receipts"]["extraction"]["payload"][
            "market_sha256"
        ] = "f" * 64
    if mutation == "missing":
        physical.pop(next(k for k in physical if k.endswith("result.json")))
    with pytest.raises((ValueError, AssertionError)):
        validate(row, indexes, physical)


def test_sql_exact_new_profile_and_full_launch_commitment():
    sql = render_sql()
    for fragment in (
        "mineagent_structured_stopped",
        "structured-factor-execution:",
        "268500992",
        "90000",
        "60 seconds",
        "strategy_input_sha256",
        "market_sha256",
        "mineagent-factor-strategy-",
        "closed_operator_trusted_engine",
    ):
        assert fragment in sql
    assert "qlib" not in sql and "direct-analysis:" not in sql
    with pytest.raises(ValueError):
        render_sql("public;DROP")


def test_issuer_import_and_build_inventory_are_complete():
    from packages.research_runtime.process_contracts import ProcessError
    from runtimes.structured_factor_engine.recipe import FILES, wrapper_inventory_sha256
    from workers.research.structured_process import run_registered_structured

    with pytest.raises(ProcessError, match="not_registered"):
        run_registered_structured()
    assert len(wrapper_inventory_sha256()) == 64
    dockerfile = Path("runtimes/structured_factor_engine/Dockerfile").read_text()
    for line in dockerfile.splitlines():
        if line.startswith("COPY "):
            assert set(line.split()[1:-1]) <= set(FILES)
    assert "runtimes/__init__.py" not in dockerfile
    assert (
        Path("runtimes/structured_factor_engine/trusted_parent.pth").read_text()
        == "/opt/tradegrad\n"
    )


@pytest.mark.parametrize("leaf", ["strategy.json", "market.json"])
def test_cold_residual_staging_is_checked_against_immutable_intent(leaf):
    import json

    row, indexes, physical = closure()
    prefix = f"research-processes/{row['owner_id']}/{row['id']}/"
    key = prefix + "inputs/" + leaf
    physical[key] = b"changed staging"

    def check(path, sha, size):
        if path == key:
            assert sha in {"a" * 64, "b" * 64}
            raise ValueError("Residual bytes mismatch")
        assert hashlib.sha256(physical[path]).hexdigest() == sha
        assert len(physical[path]) == size

    with pytest.raises(ValueError, match="Residual bytes mismatch"):
        validate_structured_closure_unit(
            row,
            indexes,
            physical,
            check,
            lambda p: json.loads(physical[p]),
            set(),
            set(),
        )


@pytest.mark.parametrize("fault", ["held", "refund", "outcome", "scope", "missing"])
def test_cold_stop_cannot_refund_or_skip_full_charge(fault):
    row, indexes, physical = closure()
    settlement = indexes["research_budget_settlements"]["settled"]
    if fault == "held":
        indexes["research_budget_reservations"][row["reservation_id"]]["state"] = (
            "reserved"
        )
    elif fault == "refund":
        settlement["charge"]["wall_milliseconds"] = 0
    elif fault == "outcome":
        settlement["outcome"] = "actual"
    elif fault == "scope":
        settlement["generation"] += 1
    else:
        indexes["research_budget_settlements"].clear()
    with pytest.raises(ValueError):
        validate(row, indexes, physical)
