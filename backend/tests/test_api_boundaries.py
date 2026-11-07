"""Real in-process HTTP checks with explicit persistence/password-work test doubles."""

import hashlib
import secrets
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from threading import Event, Lock
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app import factor_import_services as imports
from backend.app import main
from backend.app.api.deps import get_current_user, get_session
from backend.app.core.config import Settings
from backend.app.core.login_limit import LoginLimiter
from backend.app.factor_import_models import FactorImportAdmission
from backend.app.models import utcnow
from packages.factors.contracts import digest


@pytest.fixture
def app():
    return main.create_app(
        Settings(
            database_url="postgresql://localhost/not_used",
            secret_key=secrets.token_urlsafe(48),
            cors_origins=["https://workbench.example"],
            max_upload_bytes=1024,
            factor_library_enabled=True,
            factor_import_enabled=True,
            factor_parser_image_id="sha256:" + "a" * 64,
        ),
        engine=MagicMock(),
    )


@pytest.mark.parametrize("path", ["factors", "rd-steers"])
def test_patch_routes_support_configured_cross_origin_preflight(app, path):
    with TestClient(app) as client:
        response = client.options(
            f"/api/v1/{path}/{uuid4()}",
            headers={
                "Origin": "https://workbench.example",
                "Access-Control-Request-Method": "PATCH",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
    assert response.status_code == 200
    assert (
        response.headers["access-control-allow-origin"] == "https://workbench.example"
    )
    assert "PATCH" in response.headers["access-control-allow-methods"]


def test_factor_ingress_refusal_has_cors_and_consistent_error_headers(app):
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/factor-packages/import",
            content=b"not admitted",
            headers={
                "Origin": "https://workbench.example",
                "Content-Type": "text/plain",
            },
        )
    assert response.status_code == 415
    assert (
        response.headers["access-control-allow-origin"] == "https://workbench.example"
    )
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-correlation-id"] == response.json()["correlation_id"]


def test_unhandled_error_retains_no_store_and_correlated_error_envelope(app):
    @app.get("/synthetic-error")
    def fail():
        raise RuntimeError("private diagnostic not for client")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/synthetic-error")
    assert response.status_code == 500
    assert "private diagnostic" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-correlation-id"] == response.json()["correlation_id"]


@pytest.mark.parametrize("replayed", [False, True])
def test_factor_import_post_and_status_never_expose_worker_lease(
    app, monkeypatch, replayed
):
    owner, lease = uuid4(), uuid4()
    content = b"opaque test archive; parser is explicitly stubbed"
    archive_sha = hashlib.sha256(content).hexdigest()
    image_id = app.state.settings.factor_parser_image_id
    previous = FactorImportAdmission(
        id=uuid4(),
        owner_id=owner,
        archive_object_id=uuid4(),
        archive_sha256=archive_sha,
        idempotency_key="replayed-import",
        request_sha256=digest(
            {
                "archive_sha256": archive_sha,
                "parser_image_id": image_id,
                "profile": "factor-import.v1",
            }
        ),
        parser_image_id=image_id,
        status="processing",
        generation=1,
        lease_token=lease,
        lease_expires_at=utcnow(),
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session = MagicMock()
    session.scalar.return_value = previous if replayed else None
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=owner)
    app.dependency_overrides[get_session] = lambda: session
    monkeypatch.setattr(imports, "publication", lambda *a, **k: nullcontext(None))
    monkeypatch.setattr(
        imports,
        "owned",
        lambda *a: (
            previous
            if a[1] is FactorImportAdmission
            else SimpleNamespace(storage_key="not-read")
        ),
    )
    monkeypatch.setattr(imports, "read_verified", lambda *a: content)
    monkeypatch.setattr(imports, "decode_package", lambda *a: None)
    monkeypatch.setattr(
        imports, "publish_object", lambda *a: SimpleNamespace(id=uuid4())
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/factor-packages/import",
            content=content,
            headers={
                "Content-Type": "application/zip",
                "Idempotency-Key": "replayed-import",
            },
        )
        assert response.status_code == 202
        assert "lease_token" not in response.json()
        if replayed:
            assert response.json()["id"] == str(previous.id)
            assert response.json()["status"] == "processing"
        response = client.get(f"/api/v1/factor-imports/{previous.id}")
        assert response.status_code == 200
        assert "lease_token" not in response.json()
    assert previous.lease_token == lease


def test_login_reserves_limit_before_concurrent_password_checks(app, monkeypatch):
    session = MagicMock()
    session.scalar.return_value = None
    app.dependency_overrides[get_session] = lambda: session
    release, started = Event(), Event()
    lock = Lock()
    calls = 0

    def slow_verify(*args):
        nonlocal calls
        with lock:
            calls += 1
            if calls == 10:
                started.set()
        if not release.wait(10):
            raise AssertionError("Password work was not released")
        return False

    monkeypatch.setattr(main, "verify_password", slow_verify)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=11) as executor:

        def login():
            return client.post(
                "/api/v1/login/access-token",
                data={
                    "username": "synthetic@example.test",
                    "password": "not-a-real-password",
                },
            )

        pending = [executor.submit(login) for _ in range(10)]
        try:
            assert started.wait(5)
            rejected = executor.submit(login).result(timeout=3)
            assert rejected.status_code == 429
            assert calls == 10
        finally:
            release.set()
        assert all(request.result(timeout=5).status_code == 401 for request in pending)
        assert login().status_code == 429


def test_login_limiter_retains_active_hosts_and_expires_without_global_reset(
    monkeypatch,
):
    clock = [0.0]
    monkeypatch.setattr("backend.app.core.login_limit.time.monotonic", lambda: clock[0])
    limiter = LoginLimiter(attempts=1, max_hosts=2)
    failed = limiter.begin("failed")
    limiter.finish(failed, successful=False)
    pending = limiter.begin("pending")
    assert limiter.begin("new") is None
    assert limiter.begin("failed") is None
    clock[0] = 60
    # Expired failures can be evicted, but unfinished password checks still count.
    assert limiter.begin("new") is not None
    assert limiter.begin("pending") is None
    limiter.finish(pending, successful=True)
    assert limiter.begin("pending") is not None


def test_login_database_failure_releases_inflight_reservation(app, monkeypatch):
    limiter = LoginLimiter(attempts=1)
    monkeypatch.setattr(main, "LoginLimiter", lambda: limiter)
    app = main.create_app(app.state.settings, engine=MagicMock())
    session = MagicMock()
    session.scalar.side_effect = RuntimeError("synthetic database failure")
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/login/access-token",
            data={
                "username": "synthetic@example.test",
                "password": "not-a-real-password",
            },
        )
    assert response.status_code == 500
    assert limiter._buckets["testclient"].inflight == 0
    assert len(limiter._buckets["testclient"].failures) == 1


def test_general_body_limit_preserves_request_correlation_id(app):
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/login/access-token",
            content=b"x" * 101025,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    assert response.status_code == 413
    assert response.headers["x-correlation-id"] == response.json()["correlation_id"]
    assert response.headers["cache-control"] == "no-store"


def test_cors_still_rejects_unconfigured_origins(app):
    with TestClient(app) as client:
        response = client.options(
            f"/api/v1/factors/{uuid4()}",
            headers={
                "Origin": "https://unapproved.example",
                "Access-Control-Request-Method": "PATCH",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize(
    "allowed_origins,origin,cookie,expected_origin",
    [
        (
            ["https://workbench.example"],
            "https://workbench.example",
            None,
            "https://workbench.example",
        ),
        (["https://workbench.example"], "https://other.example", None, None),
        (["*"], "https://other.example", None, "*"),
        (["*"], "https://other.example", "synthetic=not-authentication", "*"),
        (["https://workbench.example"], None, None, None),
        ([], "https://workbench.example", None, None),
    ],
)
def test_unhandled_errors_apply_the_same_cors_policy_as_normal_responses(
    app, allowed_origins, origin, cookie, expected_origin
):
    app = main.create_app(
        app.state.settings.model_copy(update={"cors_origins": allowed_origins}),
        engine=MagicMock(),
    )

    @app.get("/synthetic-cors-error")
    def fail():
        raise RuntimeError("private diagnostic not for client")

    headers = {}
    if origin is not None:
        headers["Origin"] = origin
    if cookie is not None:
        headers["Cookie"] = cookie
    with TestClient(app, raise_server_exceptions=False) as client:
        normal = client.get("/api/v1/health/live", headers=headers)
        error = client.get("/synthetic-cors-error", headers=headers)
    assert error.status_code == 500
    assert error.json()["code"] == "internal_error"
    assert error.headers["x-correlation-id"] == error.json()["correlation_id"]
    assert error.headers["cache-control"] == "no-store"
    assert error.headers.get("access-control-allow-origin") == expected_origin
    assert "access-control-allow-credentials" not in error.headers
    for header in (
        "access-control-allow-origin",
        "access-control-allow-credentials",
        "access-control-expose-headers",
        "vary",
    ):
        assert error.headers.get(header) == normal.headers.get(header)
    if expected_origin is not None:
        assert error.headers["access-control-expose-headers"] == "X-Correlation-ID"
        assert "origin" in {
            part.strip().lower() for part in error.headers["vary"].split(",")
        }
