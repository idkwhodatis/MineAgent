"""Owner/scoped immutable view checks with real codecs and explicit DB doubles."""

from datetime import UTC, datetime
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app.models import ResearchRun
from backend.app.oe_structured_models import OEStructuredSubmission
from backend.app.oe_structured_run_views import read_owned_run
from backend.app.research_broker_models import ResearchBudgetControl
from backend.app.services import DomainError
from packages.research_runtime.budget_contracts import BudgetVector
from tests.openevolve.test_structured_persisted_identity import graph
from tests.openevolve.test_structured_routes import api as api_fixture


@pytest.fixture
def api(monkeypatch):
    return api_fixture.__wrapped__(monkeypatch)


def view_graph():
    _, run, spec, _, _, rows = graph()
    now = datetime.now(UTC)
    run.status, run.active_attempt_id = "queued", None
    run.created_at = run.updated_at = now
    run.idempotency_key = "authenticated-normal-request"
    declaration = OEStructuredSubmission(
        id=spec.submission_id,
        owner_id=run.owner_id,
        run_id=run.id,
        idempotency_key=run.idempotency_key,
        request_canonical=spec.request_canonical,
        request_sha256=spec.request_sha256,
        preflight_sha256=spec.preflight_sha256,
        config_sha256=spec.config_sha256,
        origin="authenticated_user_submission",
    )
    budget = NS(
        owner_id=run.owner_id,
        run_id=run.id,
        limits=spec.config["budget"],
        used=BudgetVector().model_dump(),
        reserved=BudgetVector().model_dump(),
    )
    rows[(ResearchRun, run.id)] = run
    rows[(OEStructuredSubmission, declaration.id)] = declaration
    entities = {
        ResearchRun: run,
        OEStructuredSubmission: declaration,
        ResearchBudgetControl: budget,
    }
    session = NS(
        get=lambda cls, key: rows.get((cls, key)),
        scalar=lambda query: entities[query.column_descriptions[0]["entity"]],
        scalars=lambda query: NS(all=lambda: ["028_oe_structured_search"]),
    )
    return session, run, declaration, budget


def test_reads_actual_status_not_submission_ack_or_seed_fallback():
    session, run, declaration, _ = view_graph()
    first = read_owned_run(session, run.owner_id, run.id)
    assert first.status == "queued" and first.active_attempt_id is None
    assert first.submission.id == declaration.id
    assert first.submission.origin_user_id == run.owner_id
    assert first.budget_used == BudgetVector()
    assert first.actions.cancel is True
    assert first.actions.pause is first.actions.resume is False
    run.status, run.active_attempt_id = "running", uuid4()
    current = read_owned_run(session, run.owner_id, run.id)
    assert (
        current.status == "running"
        and current.active_attempt_id == run.active_attempt_id
    )
    assert "result" not in current.model_dump() and "error" not in current.model_dump()


@pytest.mark.parametrize("which", ["owner", "run", "legacy"])
def test_wrong_owner_id_and_legacy_never_enter_normal_view(which):
    session, run, _, _ = view_graph()
    owner_id, run_id = run.owner_id, run.id
    if which == "owner":
        owner_id = uuid4()
    elif which == "run":
        run_id = uuid4()
    else:
        run.engine = "legacy"
    with pytest.raises(DomainError):
        read_owned_run(session, owner_id, run_id)


@pytest.mark.parametrize(
    "which",
    [
        "declaration_owner",
        "declaration_hash",
        "budget_owner",
        "budget_fields",
        "budget_overrun",
        "unknown_status",
    ],
)
def test_mutated_persisted_metadata_is_not_a_public_view(which):
    session, run, declaration, budget = view_graph()
    if which == "declaration_owner":
        declaration.owner_id = uuid4()
    elif which == "declaration_hash":
        declaration.request_sha256 = "0" * 64
    elif which == "budget_owner":
        budget.owner_id = uuid4()
    elif which == "budget_fields":
        budget.used.pop("calls")
    elif which == "budget_overrun":
        budget.used["process_slots"] = budget.limits["process_slots"] + 1
    else:
        run.status = "invented_success"
    with pytest.raises(DomainError):
        read_owned_run(session, run.owner_id, run.id)


def test_http_uses_explicit_normal_read_route_and_authenticated_owner(api, monkeypatch):
    from backend.app import oe_structured_routes as routes

    client, _, user, headers, _ = api
    session, run, _, _ = view_graph()
    view = read_owned_run(session, run.owner_id, run.id)
    called = []

    def read(_session, owner_id, run_id):
        called.append((owner_id, run_id))
        return view

    monkeypatch.setattr(routes, "read_owned_run", read)
    path = "/api/v1/engine-runs/structured-search/runs/" + str(run.id)
    assert client.get(path).status_code == 401
    assert not called
    response = client.get(path, headers=headers)
    assert response.status_code == 200, response.text
    assert called == [(user.id, run.id)]
    assert response.json()["status"] == "queued"
