"""Actual HTTP parsing/auth/commit boundaries using inert session doubles, not PG."""

from datetime import UTC, datetime
from hashlib import sha256
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.app import rdagent_steer_routes as routes
from backend.app.api.deps import get_current_user, get_session
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes
from packages.rdagent_adapter.steer import SteerVersion


@pytest.fixture
def http(monkeypatch):
    owner = NS(id=uuid4())
    session = NS(commits=0)

    def commit():
        session.commits += 1

    session.commit = commit
    app = FastAPI()
    app.include_router(routes.router)

    @app.exception_handler(DomainError)
    async def handler(request, exc):
        return JSONResponse({"code": exc.code}, status_code=exc.status_code)

    app.dependency_overrides[get_current_user] = lambda: owner
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        yield app, client, owner, session


def saved(owner, text="方向 😀"):
    value = SteerVersion(
        steer_id=uuid4(),
        version_id=uuid4(),
        ordinal=1,
        kind="direction",
        text=text,
        text_sha256=sha256(text.encode()).hexdigest(),
    )
    root = NS(
        id=value.steer_id,
        owner_id=owner.id,
        revision=1,
        disabled=False,
        current_version_id=value.version_id,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    version = NS(
        id=value.version_id,
        owner_id=owner.id,
        steer_id=value.steer_id,
        ordinal=1,
        kind=value.kind,
        text=text,
        text_sha256=value.text_sha256,
        content_sha256=value.sha256,
        content_canonical=canonical_bytes(value).decode(),
    )
    return root, version


def test_create_preserves_unicode_and_commits_before_response(http, monkeypatch):
    _, client, owner, session = http
    root, version = saved(owner)

    def create(actual, who, body):
        assert actual is session and who == owner.id and body.text == version.text
        return root, version

    monkeypatch.setattr(routes.service, "create_steer", create)
    response = client.post(
        "/rd-steers", json={"kind": "direction", "text": version.text}
    )
    assert response.status_code == 201 and session.commits == 1
    assert response.json()["current"]["text"] == version.text


@pytest.mark.parametrize(
    "body",
    [
        '{"kind":"direction","text":"a","text":"b"}',
        '{"kind":"direction","text":"a","owner_id":"injected"}',
        '{"kind":"direction","text":"a","disabled":false}',
    ],
)
def test_duplicate_or_extra_body_never_calls_writer(http, monkeypatch, body):
    _, client, _, session = http
    monkeypatch.setattr(
        routes.service, "create_steer", lambda *a: pytest.fail("writer called")
    )
    assert (
        client.post(
            "/rd-steers", content=body, headers={"Content-Type": "application/json"}
        ).status_code
        == 422
    )
    assert session.commits == 0


def test_stale_edit_and_foreign_owner_errors_do_not_commit(http, monkeypatch):
    _, client, _, session = http
    for status, code in [(409, "steer_edit_conflict"), (404, "not_found")]:

        def denied(*args, code=code, status=status):
            raise DomainError(code, "rejected", status)

        monkeypatch.setattr(routes.service, "edit_steer", denied)
        response = client.patch(
            f"/rd-steers/{uuid4()}",
            json={"kind": "hypothesis", "text": "test", "expected_revision": 1},
        )
        assert response.status_code == status and response.json()["code"] == code
    assert session.commits == 0


def test_commit_failure_returns_no_created_version(http, monkeypatch):
    _, client, owner, session = http
    monkeypatch.setattr(routes.service, "create_steer", lambda *a: saved(owner))

    def fail():
        raise RuntimeError("commit failed")

    session.commit = fail
    with pytest.raises(RuntimeError, match="commit failed"):
        client.post("/rd-steers", json={"kind": "direction", "text": "test"})


def test_schema_unavailable_is_explicit_not_enabled(http, monkeypatch):
    _, client, _, _ = http

    def denied(*args):
        raise DomainError("steer_schema_unavailable", "unavailable", 409)

    monkeypatch.setattr(routes.service, "require_schema", denied)
    assert client.get("/rd-steers/capabilities").json() == {
        "can_manage": False,
        "reason_code": "steer_schema_unavailable",
    }
    assert client.get("/rd-steers").status_code == 409


def test_anonymous_cannot_manage_or_read(http):
    app, client, _, session = http
    del app.dependency_overrides[get_current_user]
    for method, path, body in [
        ("GET", "/rd-steers", None),
        ("GET", "/rd-steers/capabilities", None),
        ("POST", "/rd-steers", {"kind": "direction", "text": "test"}),
    ]:
        assert client.request(method, path, json=body).status_code == 401
    assert session.commits == 0
