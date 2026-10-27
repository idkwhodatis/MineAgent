"""Cold evaluation input and failure linkage; process validation is separate."""

import hashlib
from types import SimpleNamespace as NS
from uuid import UUID, uuid4

import pytest

from backend.app.openevolve_backup_evaluations import validate_evaluations
from packages.openevolve_adapter.checkpoint_native import canonical
from packages.openevolve_adapter.contracts import SearchSpec
from runtimes.openevolve_evaluator.journal import NativeStop
from tests.openevolve.test_operational_native_process import case as native_case


def setup(monkeypatch, tmp_path):
    c = native_case.__wrapped__(monkeypatch, tmp_path)

    def row(value):
        return {k: str(v) if isinstance(v, UUID) else v for k, v in vars(value).items()}

    evaluation = row(c.evaluation)
    evaluation.update(
        candidate_id=str(uuid4()),
        phase="seed",
        original_strategy_id=None,
        prepared_strategy_id=None,
        logical_ticket_id=str(uuid4()),
    )
    prep = {**evaluation, "admission_id": evaluation["process_admission_id"]}
    retained = row(c.binding)
    scope = {
        k: evaluation[k] for k in ("owner_id", "run_id", "attempt_id", "generation")
    }
    stop = NativeStop(
        launch_sha256="a" * 64,
        identity_sha256="b" * 64,
        container_id="c" * 64,
        confirmed_absent=True,
        kernel_identity_absent=True,
        observed_at=c.intent.not_after,
        deadline_bound_met=True,
    )
    stop_row = {
        **scope,
        "id": str(uuid4()),
        "kind": "stop",
        "admission_id": evaluation["process_admission_id"],
        "payload": stop.model_dump(mode="json"),
    }
    observed = {
        "schema_version": "mineagent.native-failure-object.v1",
        "evaluation_id": evaluation["id"],
        "failure_code": "invalid_result",
    }
    raw = canonical(observed)
    outcome = {
        **scope,
        "id": str(uuid4()),
        "evaluation_id": evaluation["id"],
        "stop_receipt_id": stop_row["id"],
        "admission_id": evaluation["process_admission_id"],
        "input_manifest_sha256": evaluation["input_manifest_sha256"],
        "bundle_object_id": str(uuid4()),
        "bundle_kind": "search_native_failure",
        "bundle_sha256": hashlib.sha256(raw).hexdigest(),
        "status": "failed",
        "native_bundle_sha256": None,
    }
    outcome["request_sha256"] = hashlib.sha256(
        canonical(
            {
                "evaluation_id": evaluation["id"],
                "stop_receipt_id": stop_row["id"],
                "evidence_sha256": outcome["bundle_sha256"],
            }
        )
    ).hexdigest()
    tables = {
        "search_evaluation_preparations": [prep],
        "search_evaluation_inputs": [retained],
        "search_evaluation_outcomes": [outcome],
        "search_evaluations": [evaluation],
        "search_candidates": [
            {
                "id": evaluation["candidate_id"],
                "kind": "seed",
                "source_sha256": evaluation["source_sha256"],
            }
        ],
    }
    budget = {
        **scope,
        "id": evaluation["reservation_id"],
        "purpose": "process_unit",
        "logical_key": "oe-native:" + evaluation["id"] + ":1",
        "state": "settled",
        "amounts": {
            "calls": 0,
            "cost_microunits": 0,
            "tokens": 0,
            "wall_milliseconds": 90000,
            "storage_bytes": 268500992,
            "mutation_slots": 0,
            "evaluator_executions": 1,
            "process_slots": 1,
        },
    }
    research = {
        "tables": {
            "research_budget_reservations": [budget],
            "research_process_receipts": [stop_row],
        }
    }
    objects = {retained["manifest_object_id"]: c.raw, outcome["bundle_object_id"]: raw}

    def read(owner, oid, kind, sha):
        assert owner == scope["owner_id"]
        value = objects[oid]
        assert hashlib.sha256(value).hexdigest() == sha
        return value

    spec = SearchSpec.model_validate_json(canonical(c.launch.bootstrap["spec"]))
    return (
        {"tables": tables},
        research,
        {evaluation["run_id"]: spec},
        {
            evaluation["process_admission_id"]: {
                "launch": NS(intent=c.intent),
                "stop": stop,
            }
        },
        read,
        lambda *_: pytest.fail("No extraction may not invent native output"),
    )


def test_real_retained_manifest_and_stopped_invalid_result(monkeypatch, tmp_path):
    assert validate_evaluations(*setup(monkeypatch, tmp_path)) == {}


@pytest.mark.parametrize(
    "damage",
    [
        "hold",
        "double_hash",
        "window",
        "admission",
        "prepared_source",
        "no_input",
        "stop",
        "success",
        "bundle",
        "request",
    ],
)
def test_cold_evaluation_cannot_substitute_native_execution(
    monkeypatch, tmp_path, damage
):
    args = setup(monkeypatch, tmp_path)
    tables = args[0]["tables"]
    evaluation, outcome = (
        tables["search_evaluations"][0],
        tables["search_evaluation_outcomes"][0],
    )
    if damage == "hold":
        args[1]["tables"]["research_budget_reservations"][0]["amounts"][
            "storage_bytes"
        ] -= 1
    elif damage == "double_hash":
        evaluation["input_manifest_sha256"] = tables["search_evaluation_inputs"][0][
            "manifest_sha256"
        ]
    elif damage == "window":
        evaluation["window_id"] = "other"
    elif damage == "admission":
        evaluation["process_admission_id"] = str(uuid4())
    elif damage == "prepared_source":
        tables["search_evaluation_preparations"][0]["source_sha256"] = "0" * 64
    elif damage == "no_input":
        tables["search_evaluation_inputs"] = []
    elif damage == "stop":
        outcome["stop_receipt_id"] = str(uuid4())
    elif damage == "success":
        outcome["status"] = "observed"
    elif damage == "bundle":
        outcome["native_bundle_sha256"] = "0" * 64
    else:
        outcome["request_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        validate_evaluations(*args)
