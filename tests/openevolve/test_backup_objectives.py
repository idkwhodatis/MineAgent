"""Exact recomputation from already verified native observations."""

import hashlib
from uuid import uuid4

import pytest

from backend.app.openevolve_backup_objectives import validate_objectives
from packages.openevolve_adapter.checkpoint_native import canonical
from packages.openevolve_adapter.objective import Observation, score
from packages.research_runtime.broker_contracts import digest
from tests.openevolve.test_contracts import complete_spec


def case(*, failed=False):
    spec = complete_spec()
    eid, oid, cid, rid = (str(uuid4()) for _ in range(4))
    scope = {
        "owner_id": str(spec.owner_id),
        "run_id": str(spec.run_id),
        "attempt_id": str(uuid4()),
        "generation": 1,
        "epoch_id": str(uuid4()),
        "candidate_id": cid,
        "source_sha256": "a" * 64,
    }
    observation = Observation(
        spec.objective.windows[0].id, uuid4(), "b" * 64, -5.0, 0.0, 0
    )
    computed = score(spec.objective, (observation,))
    status, value, reason = (
        ("execution_failed", None, "native_error")
        if failed
        else ("eligible", computed.combined_score, computed.reason)
    )
    feedback = canonical(
        {"status": status, "combined_score": value, "reason": reason}
    ).decode()
    feedback_sha = hashlib.sha256(feedback.encode()).hexdigest()
    evidence = {
        "schema_version": "mineagent.search-objective-object.v1",
        "candidate_id": cid,
        "source_sha256": scope["source_sha256"],
        "objective_sha256": digest(spec.objective),
        "evaluation_ids": [eid],
        "status": status,
        "combined_score": value,
        "reason": reason,
        "feedback": feedback,
        "feedback_sha256": feedback_sha,
    }
    raw = canonical(evidence)
    receipt = {
        **scope,
        "id": rid,
        "objective_sha256": digest(spec.objective),
        "evidence_object_id": str(uuid4()),
        "evidence_sha256": hashlib.sha256(raw).hexdigest(),
        "status": status,
        "combined_score": value,
        "feedback": feedback,
        "feedback_sha256": feedback_sha,
    }
    evaluation = {
        **scope,
        "id": eid,
        "window_id": spec.objective.windows[0].id,
        "execution_sha256": digest(spec.execution),
    }
    outcome = {
        "id": oid,
        "evaluation_id": eid,
        "status": "failed" if failed else "observed",
    }
    member = {
        **scope,
        "objective_receipt_id": rid,
        "evaluation_id": eid,
        "outcome_id": oid,
        "ordinal": 0,
        "window_id": evaluation["window_id"],
        "execution_sha256": evaluation["execution_sha256"],
    }
    tables = {
        "search_objective_receipts": [receipt],
        "search_objective_evaluations": [member],
        "search_evaluations": [evaluation],
        "search_evaluation_outcomes": [outcome],
    }
    return (
        tables,
        {str(spec.run_id): spec},
        {} if failed else {eid: observation},
        lambda *_: raw,
    )


@pytest.mark.parametrize("failed", [False, True])
def test_real_signed_and_failed_objective_recomputed(failed):
    validate_objectives(*case(failed=failed))


@pytest.mark.parametrize(
    "damage",
    [
        "score",
        "boolean",
        "feedback",
        "scope",
        "order",
        "window",
        "missing",
        "unverified",
    ],
)
def test_objective_cannot_be_claimed_by_row(damage):
    tables, specs, observations, read = case()
    receipt = tables["search_objective_receipts"][0]
    member = tables["search_objective_evaluations"][0]
    if damage == "score":
        receipt["combined_score"] = 0.0
    elif damage == "boolean":
        receipt["combined_score"] = False
    elif damage == "feedback":
        receipt["feedback"] = "invented"
    elif damage == "scope":
        member["generation"] += 1
    elif damage == "order":
        member["ordinal"] = 1
    elif damage == "window":
        member["window_id"] = "other"
    elif damage == "missing":
        tables["search_objective_evaluations"] = []
    else:
        observations = {}
    with pytest.raises(ValueError):
        validate_objectives(tables, specs, observations, read)
