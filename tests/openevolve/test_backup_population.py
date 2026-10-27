"""Cold population ancestry/host-feedback barriers, not native execution proof."""

import hashlib
import json
from uuid import uuid4

import pytest

from backend.app.openevolve_backup_population import validate_population
from packages.openevolve_adapter.checkpoint_native import _normalize, state_sha256
from tests.openevolve.test_checkpoint_evidence import fixture


def case():
    files, _ = fixture()
    program = json.loads(next(v for k, v in files.items() if k.startswith("programs/")))
    program["artifacts_json"] = json.dumps({"native_feedback": "observed"})
    state = _normalize(json.loads(files["metadata.json"]), {program["id"]: program})
    eid, cid, oid = (str(uuid4()) for _ in range(3))
    tables = {
        "search_candidates": [
            {
                "id": cid,
                "epoch_id": eid,
                "upstream_program_id": program["id"],
                "source_sha256": hashlib.sha256(program["code"].encode()).hexdigest(),
                "kind": "seed",
            }
        ],
        "search_candidate_grants": [],
        "search_iteration_intents": [],
        "search_objective_acknowledgements": [
            {"epoch_id": eid, "candidate_id": cid, "objective_receipt_id": oid}
        ],
        "search_objective_receipts": [
            {
                "epoch_id": eid,
                "id": oid,
                "status": "eligible",
                "combined_score": -5.0,
                "feedback": "observed",
                "feedback_sha256": hashlib.sha256(b"observed").hexdigest(),
            }
        ],
    }
    return state, eid, tables


def test_signed_native_population_bound_to_exact_source_and_feedback():
    state, eid, tables = case()
    assert validate_population(state, eid, 0, state_sha256(state), tables) == state


@pytest.mark.parametrize(
    "fault",
    [
        "unregistered",
        "source",
        "wrong_epoch",
        "unacknowledged",
        "score",
        "feedback",
        "ineligible",
        "ancestry",
        "future",
        "missing_grant",
        "duplicate_program",
    ],
)
def test_population_cannot_invent_observed_objective(fault):
    state, eid, tables = case()
    program = next(iter(state["programs"].values()))
    candidate = tables["search_candidates"][0]
    receipt = tables["search_objective_receipts"][0]
    if fault == "unregistered":
        tables["search_candidates"] = []
    elif fault == "source":
        candidate["source_sha256"] = "0" * 64
    elif fault == "wrong_epoch":
        candidate["epoch_id"] = str(uuid4())
    elif fault == "unacknowledged":
        tables["search_objective_acknowledgements"] = []
    elif fault == "score":
        receipt["combined_score"] = -4.0
    elif fault == "feedback":
        program["artifacts_json"] = json.dumps({"native_feedback": "invented"})
    elif fault == "ineligible":
        receipt["status"] = "execution_failed"
    elif fault == "ancestry":
        program["generation"] = 1
    elif fault == "future":
        program["iteration_found"] = 1
    elif fault == "missing_grant":
        candidate.update(kind="mutation", parent_candidate_id=str(uuid4()))
    else:
        tables["search_candidates"].append({**candidate, "id": str(uuid4())})
    with pytest.raises(ValueError):
        validate_population(state, eid, 0, state_sha256(state), tables)
