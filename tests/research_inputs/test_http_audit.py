"""Real FastAPI metadata boundary with inert sessions; not PG/schema acceptance."""

from datetime import UTC, datetime
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.app import research_input_routes as routes
from backend.app.api.deps import get_current_user, get_session
from backend.app.services import DomainError
from packages.research_inputs.contracts import InputVersionRef


@pytest.fixture
def http():
    owner, session = NS(id=uuid4()), NS()
    workflow, stage = uuid4(), uuid4()
    now = datetime.now(UTC)
    saved = NS(
        derivation_id=uuid4(),
        workflow_id=workflow,
        stage_id=stage,
        operation="external_normalization",
        source_sha256="a" * 64,
        raw_input_sha256="b" * 64,
        mapping_sha256="c" * 64,
        status="prepared",
        error_code=None,
        prepared_at=now,
        started_at=None,
        terminal_at=None,
        outputs=(),
        raw_body="private raw input must not leave this boundary",
    )
    app = FastAPI()
    app.include_router(routes.router)

    @app.exception_handler(DomainError)
    async def handler(request, exc):
        return JSONResponse({"code": exc.code}, status_code=exc.status_code)

    app.dependency_overrides[get_current_user] = lambda: owner
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        yield NS(
            app=app,
            client=client,
            owner=owner,
            session=session,
            saved=saved,
            url=f"/research-workflows/{workflow}/stages/{stage}/input-derivation",
        )


@pytest.mark.parametrize(
    "status", ["prepared", "started_no_terminal_receipt", "succeeded", "failed"]
)
def test_exact_owner_receipt_state_never_infers_live_running(http, monkeypatch, status):
    h = http
    h.saved.status = status
    if status != "prepared":
        h.saved.started_at = h.saved.prepared_at
    if status in {"succeeded", "failed"}:
        h.saved.terminal_at = h.saved.prepared_at
    if status == "failed":
        h.saved.error_code = "invalid_json_or_mapping"
    if status == "succeeded":
        h.saved.outputs = (
            InputVersionRef(
                origin="normalized_external_proposal",
                record_id=uuid4(),
                version_id=uuid4(),
                content_sha256="d" * 64,
            ),
        )

    def read(session, owner, workflow, stage):
        assert session is h.session and owner == h.owner.id
        assert (workflow, stage) == (h.saved.workflow_id, h.saved.stage_id)
        return h.saved

    monkeypatch.setattr(routes, "read_derivation_history", read)
    response = h.client.get(h.url)
    assert response.status_code == 200
    assert response.json()["status"] == status
    assert (
        "raw_body" not in response.json() and "private raw input" not in response.text
    )
    assert (
        response.json()["qualification"]
        == "receipt_metadata_not_live_status_or_content_approval"
    )


def test_missing_or_foreign_derivation_does_not_fabricate_prepared_state(
    http, monkeypatch
):
    def denied(*args):
        raise DomainError("not_found", "Input derivation not found", 404)

    monkeypatch.setattr(routes, "read_derivation_history", denied)
    assert http.client.get(http.url).status_code == 404


@pytest.mark.parametrize("drift", ["scope", "unsafe_error", "manual_origin"])
def test_bad_projection_is_withheld_not_partially_returned(http, monkeypatch, drift):
    saved = http.saved
    if drift == "scope":
        saved.stage_id = uuid4()
    elif drift == "unsafe_error":
        saved.status, saved.terminal_at = "failed", saved.prepared_at
        saved.error_code = "https://private.invalid/token=private"
    else:
        saved.status, saved.terminal_at = "succeeded", saved.prepared_at
        saved.outputs = (
            InputVersionRef(
                origin="manual_user_submission",
                record_id=uuid4(),
                version_id=uuid4(),
                content_sha256="d" * 64,
            ),
        )
    monkeypatch.setattr(routes, "read_derivation_history", lambda *args: saved)
    response = http.client.get(http.url)
    assert response.status_code == 409
    assert response.json() == {"code": "input_derivation_changed"}
    assert "private" not in response.text


def test_installed_schema_without_input_capability_stays_closed(http):
    http.session.scalars = lambda query: NS(all=lambda: ["unregistered_future"])
    http.session.scalar = lambda query: pytest.fail(
        "No input table may be read before capability"
    )
    response = http.client.get(http.url)
    assert response.status_code == 409
    assert response.json()["code"] == "research_input_storage_unavailable"


def test_authentication_is_required_before_reading_receipts(http, monkeypatch):
    del http.app.dependency_overrides[get_current_user]
    monkeypatch.setattr(
        routes,
        "read_derivation_history",
        lambda *args: pytest.fail("Unauthorized reader invoked"),
    )
    assert http.client.get(http.url).status_code in {401, 403}


@pytest.mark.parametrize("raw", [b"", b"not JSON", b'{"items":[]}', b"x" * 65536])
def test_raw_intake_preserves_exact_bounded_bytes_without_claiming_normalization(
    http, monkeypatch, raw
):
    import hashlib

    h = http
    factory, settings = object(), object()
    h.app.state.session_factory, h.app.state.settings = factory, settings
    identity = uuid4()
    calls = []

    def retain(actual_factory, actual_settings, *, owner_id, raw):
        assert (
            actual_factory is factory
            and actual_settings is settings
            and owner_id == h.owner.id
        )
        calls.append(raw)
        return NS(
            id=identity, sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw)
        )

    monkeypatch.setattr(routes, "retain_external_object", retain)
    response = h.client.post(
        "/research-inputs/external-objects",
        content=raw,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 201 and calls == [raw]
    assert response.json() == {
        "id": str(identity),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size_bytes": len(raw),
        "qualification": "raw_retained_not_normalized",
        "workflow_created": False,
    }


def test_streamed_overflow_never_calls_publication(http, monkeypatch):
    monkeypatch.setattr(
        routes,
        "retain_external_object",
        lambda *args, **kwargs: pytest.fail("Oversized raw input reached storage"),
    )
    response = http.client.post(
        "/research-inputs/external-objects", content=b"x" * 65537
    )
    assert response.status_code == 413
    assert response.json() == {"code": "external_input_too_large"}


def test_raw_intake_never_acknowledges_failed_factory_commit(http, monkeypatch):
    http.app.state.session_factory, http.app.state.settings = object(), object()

    def denied(*args, **kwargs):
        raise DomainError("research_input_storage_unavailable", "closed", 409)

    monkeypatch.setattr(routes, "retain_external_object", denied)
    response = http.client.post("/research-inputs/external-objects", content=b"{}")
    assert response.status_code == 409 and "id" not in response.json()


@pytest.mark.parametrize("normalized", [False, True])
def test_explicit_content_is_owner_bound_attachment_not_json_wrapper(
    http, monkeypatch, normalized
):
    import hashlib

    h = http
    identity = uuid4()
    raw = (
        b"<script>not executable</script>\xff"
        if not normalized
        else b'{"text":"<script>passive</script>"}'
    )
    sha = hashlib.sha256(raw).hexdigest()
    h.app.state.session_factory = object()
    h.app.state.settings = object()

    def read(factory, settings, **kwargs):
        assert factory is h.app.state.session_factory
        assert settings is h.app.state.settings
        assert kwargs == {
            "owner_id": h.owner.id,
            "expected_sha256": sha,
            "version_id" if normalized else "object_id": identity,
        }
        return NS(content=raw, sha256=sha, size_bytes=len(raw))

    monkeypatch.setattr(
        routes,
        "read_normalized_content" if normalized else "read_external_content",
        read,
    )
    prefix = "versions" if normalized else "external-objects"
    response = h.client.get(
        f"/research-inputs/{prefix}/{identity}/content?expected_sha256={sha}"
    )
    assert response.status_code == 200
    assert response.content == raw
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-content-sha256"] == sha


@pytest.mark.parametrize("status", [404, 409])
def test_content_foreign_or_changed_never_returns_partial_bytes(
    http, monkeypatch, status
):
    def read(*args, **kwargs):
        raise DomainError(
            "not_found" if status == 404 else "input_content_inconsistent",
            "private",
            status,
        )

    http.app.state.session_factory = object()
    http.app.state.settings = object()
    monkeypatch.setattr(routes, "read_external_content", read)
    response = http.client.get(
        f"/research-inputs/external-objects/{uuid4()}/content?expected_sha256={'a' * 64}"
    )
    assert response.status_code == status
    assert "private" not in response.text


def test_content_requires_exact_reference_before_reader(http, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Malformed commitment reached reader")

    monkeypatch.setattr(routes, "read_external_content", forbidden)
    assert (
        http.client.get(
            f"/research-inputs/external-objects/{uuid4()}/content?expected_sha256=bad"
        ).status_code
        == 422
    )
