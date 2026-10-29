"""Actual HTTP/parser/transaction boundary with inert services, not PG acceptance."""

from contextlib import contextmanager
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.app import research_workflow_routes as routes
from backend.app.api.deps import get_current_user
from backend.app.research_workflow_public_schemas import (
    WorkflowPreflight,
    WorkflowSubmit,
)
from backend.app.services import DomainError
from packages.research_workflow.admission import WorkflowCreated
from tests.research_workflow.test_public_admission import declaration as declaration  # noqa: PLC0414


@pytest.fixture
def http(declaration, monkeypatch):
    f = declaration
    events = []
    session = NS(commit=lambda: events.append("commit"))

    @contextmanager
    def factory():
        events.append("session")
        yield session

    factory.kw = {"bind": object()}

    @contextmanager
    def guard(engine, root):
        assert engine is factory.kw["bind"]
        events.append("guard")
        yield
        events.append("guard_exit")

    monkeypatch.setattr(routes, "shared_lifecycle", guard)
    app = FastAPI()
    app.state.session_factory = factory
    app.state.settings = NS(storage_root="/inert")
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: NS(id=f.p.owner_id)

    @app.exception_handler(DomainError)
    async def error(request, exc):
        return JSONResponse({"code": exc.code}, status_code=exc.status_code)

    preflight = WorkflowPreflight(
        **{
            key: "a" * 64
            for key in (
                "request_sha256",
                "preflight_sha256",
                "plan_sha256",
                "provider_preflight_sha256",
                "resolved_inputs_sha256",
                "runtime_profile_sha256",
            )
        }
    )
    created = WorkflowCreated(
        workflow_id=uuid4(),
        input_revision_id=uuid4(),
        request_sha256="b" * 64,
        status="blocked",
        current_stage_id=None,
    )

    def prepare(session_arg, owner, body, settings):
        assert session_arg is session and owner == f.p.owner_id and body == f.request
        events.append("prepare")
        return NS(preflight=preflight)

    def submit(session_arg, owner, body, settings):
        assert (
            session_arg is session
            and owner == f.p.owner_id
            and body.request == f.request
        )
        events.append("submit")
        return created

    monkeypatch.setattr(routes, "prepare_workflow", prepare)
    monkeypatch.setattr(routes, "submit_workflow", submit)
    with TestClient(app) as client:
        yield NS(**locals())


def test_preflight_accepts_real_json_uuid_refs_without_creating_workflow(http):
    h = http
    result = h.client.post(
        "/research-workflows/preflight", json=h.f.request.model_dump(mode="json")
    )
    assert result.status_code == 200
    assert (
        result.json()["qualification"]
        == "fresh_declaration_not_stage_execution_or_release"
    )
    assert h.events == ["guard", "session", "prepare", "guard_exit"]


def test_creation_ack_follows_one_commit_inside_guard_and_remains_blocked(http):
    h = http
    body = WorkflowSubmit(
        idempotency_key="exact-1", request=h.f.request, preflight_sha256="a" * 64
    )
    result = h.client.post("/research-workflows", json=body.model_dump(mode="json"))
    assert result.status_code == 201 and result.json()["status"] == "blocked"
    assert result.json()["current_stage_id"] is None
    assert h.events == ["guard", "session", "submit", "commit", "guard_exit"]


def test_failed_commit_returns_no_created_receipt(http):
    def fail():
        raise RuntimeError("inert commit failure")

    http.session.commit = fail
    body = WorkflowSubmit(
        idempotency_key="exact-1", request=http.f.request, preflight_sha256="a" * 64
    )
    with pytest.raises(RuntimeError, match="inert commit failure"):
        http.client.post("/research-workflows", json=body.model_dump(mode="json"))


@pytest.mark.parametrize("field", ["owner_id", "runtime", "plan", "image_id"])
def test_public_body_rejects_internal_authority_knobs(http, field):
    value = http.f.request.model_dump(mode="json")
    value[field] = str(uuid4())
    assert (
        http.client.post("/research-workflows/preflight", json=value).status_code == 422
    )
    assert not http.events


def test_body_is_stream_bounded_and_duplicate_keys_rejected(http):
    assert (
        http.client.post(
            "/research-workflows/preflight", content=b" " * 131073
        ).status_code
        == 413
    )
    assert (
        http.client.post(
            "/research-workflows/preflight",
            content=b'{"mode":"continuous","mode":"pause_after_stage"}',
        ).status_code
        == 422
    )
    assert not http.events


def test_control_scope_mismatch_and_cas_failure_are_not_success(http, monkeypatch):
    workflow = uuid4()
    body = {
        "workflow_id": str(uuid4()),
        "expected_revision": 1,
        "idempotency_key": "command",
        "action": "pause_after_stage",
    }
    assert (
        http.client.post(
            f"/research-workflows/{workflow}/commands", json=body
        ).status_code
        == 422
    )
    body["workflow_id"] = str(workflow)

    def conflict(*args):
        raise DomainError("workflow_revision_changed", "inert", 409)

    monkeypatch.setattr(routes, "request_control", conflict)
    result = http.client.post(f"/research-workflows/{workflow}/commands", json=body)
    assert (
        result.status_code == 409
        and result.json()["code"] == "workflow_revision_changed"
    )
    assert "commit" not in http.events


def test_foreign_workflow_read_stays_404_and_no_write(http, monkeypatch):
    def missing(session, owner, workflow, settings):
        assert owner == http.f.p.owner_id
        raise DomainError("not_found", "inert", 404)

    monkeypatch.setattr(routes, "read_workflow", missing)
    assert http.client.get(f"/research-workflows/{uuid4()}").status_code == 404
    assert "commit" not in http.events


def test_openapi_has_real_bounded_body_without_dangling_defs_or_internal_plan(http):
    import json

    schema = http.app.openapi()["paths"]["/research-workflows/preflight"]["post"][
        "requestBody"
    ]
    raw = json.dumps(schema)
    assert "#/$defs" not in raw and '"owner_id"' not in raw and '"runtime"' not in raw
    assert (
        "selected_steer_version_ids" in raw
    )  # validator requires empty; ordered refs own guidance.


def test_selected_snapshot_read_is_not_current_stage_or_a_write(http, monkeypatch):
    from packages.research_workflow.contracts import WorkflowInputSnapshot

    workflow = uuid4()
    saved = WorkflowInputSnapshot(
        owner_id=http.f.p.owner_id, workflow_id=workflow, revision_id=uuid4()
    )

    def selected(session, owner, workflow_id):
        assert owner == saved.owner_id and workflow_id == workflow
        return saved

    monkeypatch.setattr(routes, "read_selected_inputs", selected)
    response = http.client.get(f"/research-workflows/{workflow}/selected-inputs")
    assert response.status_code == 200
    assert response.json() == saved.model_dump(mode="json")
    assert "commit" not in http.events


def test_selection_cas_conflict_does_not_acknowledge_application(http, monkeypatch):
    workflow = uuid4()

    def conflict(session, owner, body, settings):
        assert owner == http.f.p.owner_id and body.workflow_id == workflow
        raise DomainError("workflow_revision_changed", "inert", 409)

    monkeypatch.setattr(routes, "select_workflow_inputs", conflict)
    response = http.client.post(
        f"/research-workflows/{workflow}/selected-inputs",
        json={
            "workflow_id": str(workflow),
            "expected_revision": 1,
            "idempotency_key": "saved-but-not-applied",
            "refs": [],
        },
    )
    assert response.status_code == 409
    assert "commit" not in http.events


def test_authentication_required_before_any_workflow_service(http):
    http.app.dependency_overrides.clear()
    response = http.client.get(f"/research-workflows/{uuid4()}")
    assert response.status_code in (401, 403)
    # Authentication may open its own DB session, but no workflow service/guard/write runs.
    assert http.events == ["session"]


def test_manual_option_uses_full_verified_version_commitment_not_text_hash(monkeypatch):
    from hashlib import sha256

    from packages.rdagent_adapter.steer import SteerVersion

    owner, steer, version_id = uuid4(), uuid4(), uuid4()
    version = SteerVersion(
        steer_id=steer,
        version_id=version_id,
        ordinal=1,
        kind="direction",
        text="Investigate dispersion",
        text_sha256=sha256(b"Investigate dispersion").hexdigest(),
    )
    root = NS(
        id=steer,
        owner_id=owner,
        current_version_id=version_id,
        disabled=False,
        revision=1,
    )
    row = object()
    session = NS(
        scalars=lambda statement: NS(all=lambda: [root]), get=lambda model, key: row
    )
    monkeypatch.setattr(routes.steer_service, "require_schema", lambda s: None)

    def decode(value, actual_owner):
        assert value is row and actual_owner == owner
        return version

    monkeypatch.setattr(routes.steer_service, "decode_version", decode)
    result = routes._manual_options(session, owner, 0, 25)
    assert result.items[0].ref.content_sha256 == version.sha256
    assert result.items[0].ref.content_sha256 != version.text_sha256
    assert result.items[0].ref.record_id == steer
    assert not result.has_more


@pytest.mark.parametrize(
    "revision,structured",
    [
        ("029_research_workflow", False),
        ("030_declared_upload_basket", False),
        ("031_workflow_structured_stage", True),
    ],
)
def test_capability_reports_metadata_preflight_not_execution(
    http, revision, structured
):
    def scalars(statement):
        assert str(statement) == "SELECT version_num FROM alembic_version"
        return NS(all=lambda: [revision])

    http.session.scalars = scalars
    response = http.client.get("/research-workflows/capabilities")
    assert response.status_code == 200
    assert response.json() == {
        "can_read": True,
        "preflight_available": True,
        "structured_preflight_available": structured,
        "reason_code": "per_request_runtime_preflight_required",
    }
    assert "commit" not in http.events


@pytest.mark.parametrize(
    "versions",
    [[], ["032_unknown"], ["031_workflow_structured_stage", "029_research_workflow"]],
)
def test_actual_capability_gate_rejects_unknown_or_multirow(http, versions):
    http.session.scalars = lambda statement: NS(all=lambda: versions)
    response = http.client.get("/research-workflows/capabilities")
    assert response.status_code == 200
    assert response.json() == {
        "can_read": False,
        "preflight_available": False,
        "structured_preflight_available": False,
        "reason_code": "workflow_unavailable",
    }
    assert "commit" not in http.events


def test_unknown_or_uninstalled_workflow_capability_is_closed(http, monkeypatch):
    def unavailable(session):
        raise DomainError("workflow_unavailable", "inert", 409)

    monkeypatch.setattr(routes, "require_schema", unavailable)
    response = http.client.get("/research-workflows/capabilities")
    assert response.status_code == 200
    assert (
        not response.json()["can_read"] and not response.json()["preflight_available"]
    )
    assert "commit" not in http.events
