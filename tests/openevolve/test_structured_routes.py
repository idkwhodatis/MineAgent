"""Actual FastAPI/auth/JSON routes with explicit service doubles, not PG acceptance."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.app import oe_structured_routes as routes
from backend.app.api.deps import get_session
from backend.app.core.security import create_access_token
from backend.app.models import User
from backend.app.oe_structured_provider_submission import (
    ProviderStructuredSearchSubmit,
    ProviderStructuredSubmissionReceipt,
)
from backend.app.services import DomainError
from tests.openevolve.test_structured_provider_contracts import declared
from tests.openevolve.test_structured_provider_submission import request as declaration


@pytest.fixture
def api(monkeypatch):
    user = NS(id=uuid4(), is_active=True)

    class Session:
        def get(self, cls, key):
            assert cls is User
            return user if key == user.id else None

    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    app.state.settings = NS(secret_key="synthetic-secret-" * 3, access_token_minutes=5)
    app.state.session_factory = object()
    app.dependency_overrides[get_session] = lambda: Session()

    @app.exception_handler(DomainError)
    async def domain_error(_, error):
        return JSONResponse(
            status_code=error.status_code,
            content={"code": error.code, "message": error.message},
        )

    called = []

    def submit(factory, owner, body, key, settings):
        called.append((owner, body, key))
        assert owner == user.id
        req = body.request()
        return ProviderStructuredSubmissionReceipt(
            id=uuid4(),
            run_id=uuid4(),
            origin_user_id=owner,
            request_sha256=req.sha256,
            preflight_sha256=body.preflight_sha256,
            normalized_request=req,
            required_process_units=req.required_process_units,
        )

    monkeypatch.setattr(routes, "submit", submit)
    client = TestClient(app)
    token = create_access_token(user.id, app.state.settings)
    headers = {"Authorization": "Bearer " + token, "Idempotency-Key": "normal-request"}
    return client, app, user, headers, called


def body():
    return ProviderStructuredSearchSubmit(
        **declaration().model_dump(), preflight_sha256="f" * 64
    ).model_dump(mode="json")


def test_bearer_owner_and_exact_normal_request_reach_committing_service(api):
    client, _, user, headers, called = api
    response = client.post(
        "/api/v1/engine-runs/structured-search", json=body(), headers=headers
    )
    assert response.status_code == 201, response.text
    assert response.json()["origin_user_id"] == str(user.id)
    assert len(called) == 1
    assert called[0][2] == "normal-request"


def test_missing_auth_never_calls_submission(api):
    client, _, _, _, called = api
    response = client.post(
        "/api/v1/engine-runs/structured-search",
        json=body(),
        headers={"Idempotency-Key": "normal-request"},
    )
    assert response.status_code == 401
    assert not called


def test_duplicate_key_rejected_before_submission(api):
    import json

    client, _, _, headers, called = api
    raw = json.dumps(body())
    raw = '{"snapshot_id":"' + str(uuid4()) + '",' + raw[1:]
    response = client.post(
        "/api/v1/engine-runs/structured-search",
        content=raw,
        headers={**headers, "Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert not called


@pytest.mark.parametrize(
    "field", ["owner_id", "base_url", "api_key", "python_source", "approved"]
)
def test_caller_cannot_override_authority_or_proposal_origin(api, field):
    client, _, _, headers, called = api
    raw = body()
    raw[field] = "caller"
    response = client.post(
        "/api/v1/engine-runs/structured-search", json=raw, headers=headers
    )
    assert response.status_code == 422
    assert not called


def test_catalog_discloses_only_owned_public_binding_not_endpoint_or_secret_name(api):
    client, app, user, headers, _ = api
    profile, *_ = declared()
    profile = profile.model_copy(
        update={
            "connection": profile.connection.model_copy(
                update={"owner_ids": (user.id,)}
            )
        }
    )
    app.state.settings.oe_structured_provider_profiles = (profile,)
    response = client.get(
        "/api/v1/engine-runs/structured-search/model-profiles", headers=headers
    )
    assert response.status_code == 200
    values = response.json()
    assert len(values) == 1
    assert values[0]["operator_available"] is False
    assert values[0]["binding_sha256"] == profile.public_binding(user.id).sha256
    assert values[0]["unavailable_reason"] == "oe_submission_disabled"
    assert "provider.example" not in response.text
    assert "MODEL_SECRET" not in response.text
    assert "api_key" not in response.text


@pytest.mark.parametrize(
    "revisions,available",
    [
        (["028_oe_structured_search"], True),
        (["026_rd_seeded_submissions"], False),
        (["028_future"], False),
        ([], False),
        (["028_oe_structured_search", "026_rd_seeded_submissions"], False),
    ],
)
def test_normal_capabilities_require_exact_installed_revision(
    api, revisions, available
):
    client, app, user, headers, _ = api

    class Session:
        def get(self, cls, key):
            assert cls is User
            return user if key == user.id else None

        def scalars(self, statement):
            assert str(statement) == "SELECT version_num FROM alembic_version"
            return NS(all=lambda: revisions)

    app.dependency_overrides[get_session] = lambda: Session()
    path = "/api/v1/engine-runs/structured-search/capabilities"
    assert client.get(path).status_code == 401
    response = client.get(path, headers=headers)
    assert response.status_code == 200
    assert response.json() == {
        "routes_available": available,
        "can_preflight": available,
        "reason_code": None if available else "runtime_schema_unavailable",
        "qualification": "metadata_not_submission_authority",
    }


@pytest.mark.parametrize("commit_failure", [False, True])
def test_cancel_ack_follows_actual_transaction_exit(api, monkeypatch, commit_failure):
    from contextlib import contextmanager

    from backend.app import oe_structured_control_services as control
    from backend.app.core import maintenance
    from backend.app.oe_structured_run_schemas import StructuredControlReceipt

    client, app, user, headers, _ = api
    run_id = uuid4()
    seen = []

    @contextmanager
    def begin():
        seen.append("begin")
        yield object()
        seen.append("commit")
        if commit_failure:
            raise DomainError("commit_failed", "not acknowledged", 503)

    @contextmanager
    def lifecycle(*_):
        yield

    app.state.settings.storage_root = "/unused"
    app.state.session_factory = NS(kw={"bind": object()}, begin=begin)
    monkeypatch.setattr(maintenance, "shared_lifecycle", lifecycle)

    def cancel(session, owner, key):
        assert (owner, key) == (user.id, run_id)
        seen.append("cancel")
        return StructuredControlReceipt(
            run_id=key, status="cancel_requested", pending=True
        )

    monkeypatch.setattr(control, "request_cancel", cancel)
    response = client.post(
        f"/api/v1/engine-runs/structured-search/runs/{run_id}/cancel", headers=headers
    )
    assert response.status_code == (503 if commit_failure else 200), response.text
    assert seen == ["begin", "cancel", "commit"]
    if not commit_failure:
        assert response.json()["pending"] is True
        assert "physical_stop_proven" not in response.json()


def test_cancel_without_auth_never_enters_service(api):
    client, _, _, _, _ = api
    response = client.post(
        f"/api/v1/engine-runs/structured-search/runs/{uuid4()}/cancel"
    )
    assert response.status_code == 401
