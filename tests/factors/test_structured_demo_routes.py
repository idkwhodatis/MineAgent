"""Normal HTTP/auth/commit boundaries; publication/SQL are explicit doubles.

Physical generator/reader/quota behavior is separately tested by demo service
cases. These are not a PostgreSQL or real research-engine acceptance claim.
"""

from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.app import structured_demo_routes as routes
from backend.app.api.deps import get_session
from backend.app.core.security import create_access_token
from backend.app.models import User
from backend.app.services import DomainError
from packages.structured_factor_strategy.demo import dataset_id


@pytest.fixture
def api(tmp_path, monkeypatch):
    user = SimpleNamespace(id=uuid4(), is_active=True)
    other = SimpleNamespace(id=uuid4(), is_active=True)
    users = {user.id: user, other.id: other}
    events = []
    settings = SimpleNamespace(
        secret_key="synthetic-secret-" * 3,
        access_token_minutes=5,
        storage_root=tmp_path,
    )
    state = SimpleNamespace(fail_commit=False, fail_publication=False, active=False)
    engine = object()

    class Session:
        def get(self, cls, identity):
            assert cls is User
            return users.get(identity)

        def get_bind(self):
            return engine

    session = Session()

    @contextmanager
    def lifecycle(actual_engine, root):
        assert actual_engine is engine and root == tmp_path
        events.append("lifecycle_enter")
        try:
            yield
        finally:
            events.append("lifecycle_exit")

    @contextmanager
    def publication(actual_session, owner, config, *, peak_bytes, peak_files):
        assert actual_session is session and config is settings
        assert owner in users and events[-1] == "lifecycle_enter"
        assert peak_bytes == routes.PUBLICATION_PEAK_BYTES
        assert peak_files == routes.PUBLICATION_PEAK_FILES
        if state.fail_publication:
            raise DomainError("factor_capacity", "Quota exhausted", 409)
        state.active = True
        events.append("publication_enter")
        admission = object()
        state.admission = admission
        try:
            yield admission
            events.append("commit")
            if state.fail_commit:
                raise DomainError("factor_storage_uncertain", "Commit uncertain", 409)
        finally:
            state.active = False
            events.append("publication_exit")

    def publish(actual_session, owner, config, *, admission):
        assert state.active and admission is state.admission
        assert actual_session is session and config is settings
        events.append("publish")
        return SimpleNamespace(
            id=dataset_id(owner),
            name="Synthetic demo",
            market="US",
            start_date="2024-03-11",
            end_date="2024-03-15",
            source="mineagent_structured_synthetic_demo",
            is_synthetic=True,
            content_sha256="a" * 64,
            manifest={"synthetic": True},
            created_at=datetime(2024, 3, 20, tzinfo=UTC),
        )

    monkeypatch.setattr(routes, "shared_lifecycle", lifecycle)
    monkeypatch.setattr(routes, "publication", publication)
    monkeypatch.setattr(routes, "publish_demo_dataset", publish)
    app = FastAPI()
    app.state.settings = settings
    app.dependency_overrides[get_session] = lambda: session
    app.include_router(routes.router, prefix="/api/v1")

    @app.exception_handler(DomainError)
    def domain_error(_, error):
        return JSONResponse(
            {"error": {"code": error.code}}, status_code=error.status_code
        )

    def headers(account=user):
        return {"Authorization": "Bearer " + create_access_token(account.id, settings)}

    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            headers=headers,
            user=user,
            other=other,
            state=state,
            events=events,
        )


URL = "/api/v1/engine-runs/demo-dataset"


def test_authenticated_normal_demo_is_explicit_and_owner_idempotent(api):
    first = api.client.post(
        URL, headers=api.headers(), json={"acknowledge_synthetic": True}
    )
    assert first.status_code == 200
    value = first.json()
    assert value["dataset"]["id"] == str(dataset_id(api.user.id))
    assert value["dataset"]["is_synthetic"] is True
    assert value["research_submitted"] is False
    assert (
        value["qualification"] == "synthetic_by_construction_not_live_or_pit_evidence"
    )
    assert value["rd_preset"]["acknowledgement_required"] is True
    assert value["rd_preset"]["window"] == {"start": "2024-03-13", "end": "2024-03-14"}
    assert api.events == [
        "lifecycle_enter",
        "publication_enter",
        "publish",
        "commit",
        "publication_exit",
        "lifecycle_exit",
    ]
    second = api.client.post(
        URL, headers=api.headers(), json={"acknowledge_synthetic": True}
    )
    assert second.status_code == 200 and second.json() == value
    different = api.client.post(
        URL, headers=api.headers(api.other), json={"acknowledge_synthetic": True}
    )
    assert different.status_code == 200
    assert different.json()["dataset"]["id"] != value["dataset"]["id"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"acknowledge_synthetic": False},
        {"acknowledge_synthetic": 1},
        {"acknowledge_synthetic": "true"},
        {"acknowledge_synthetic": True, "owner_id": "other"},
        {"acknowledge_synthetic": True, "submit_research": True},
    ],
)
def test_missing_or_injected_ack_does_not_publish(api, body):
    result = api.client.post(URL, headers=api.headers(), json=body)
    assert result.status_code == 422 and api.events == []


def test_duplicate_keys_do_not_alias_ack(api):
    result = api.client.post(
        URL,
        headers={**api.headers(), "Content-Type": "application/json"},
        content='{"acknowledge_synthetic":false,"acknowledge_synthetic":true}',
    )
    assert result.status_code == 422 and api.events == []


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer invalid"}])
def test_existing_bearer_authentication_required(api, headers):
    result = api.client.post(URL, headers=headers, json={"acknowledge_synthetic": True})
    assert result.status_code == 401 and api.events == []


@pytest.mark.parametrize(
    "failure,code",
    [
        ("fail_commit", "factor_storage_uncertain"),
        ("fail_publication", "factor_capacity"),
    ],
)
def test_no_ready_ack_before_successful_publication_commit(api, failure, code):
    setattr(api.state, failure, True)
    result = api.client.post(
        URL, headers=api.headers(), json={"acknowledge_synthetic": True}
    )
    assert result.status_code == 409 and result.json() == {"error": {"code": code}}
    assert "dataset" not in result.json()


def test_route_is_registered_in_normal_application():
    import ast
    from pathlib import Path

    tree = ast.parse(Path("backend/app/main.py").read_text())
    assert any(
        isinstance(node, ast.ImportFrom)
        and node.module == "backend.app.structured_demo_routes"
        for node in ast.walk(tree)
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "include_router"
        and node.args
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "structured_demo_router"
        for node in ast.walk(tree)
    )
