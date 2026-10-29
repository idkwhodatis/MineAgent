"""Actual retained files/native flock with scoped DB/release-seam doubles, not PG."""

import hashlib
from contextlib import contextmanager
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from test_feedback_resolution import setup
from test_http_audit import http as audit_http

from backend.app import research_input_content as external
from backend.app import research_input_feedback_content as content
from backend.app import research_input_feedback_resolution as resolver
from backend.app import research_input_routes as routes
from backend.app.factor_native_io import native_storage_guard
from backend.app.models import User
from backend.app.research_input_models import (
    ResearchInputObject,
    ResearchInputRecord,
    ResearchInputVersion,
)
from backend.app.services import DomainError


@pytest.fixture
def http():
    yield from audit_http.__wrapped__()


@pytest.fixture
def retained(monkeypatch, tmp_path):
    owner, ref, rows, session, settings, path, log, output = setup(
        monkeypatch, tmp_path
    )
    rows[User] = NS(id=owner, is_active=True)
    session.scalar = lambda statement: rows[ResearchInputVersion]
    monkeypatch.setattr(external, "require_input_capability", lambda s: None)
    factory = object()

    @contextmanager
    def history(actual_factory, actual_owner, actual_settings):
        assert (actual_factory, actual_owner, actual_settings) == (
            factory,
            owner,
            settings,
        )
        with native_storage_guard(settings.storage_root):
            yield session

    monkeypatch.setattr(content, "historical_session", history)
    return NS(
        owner=owner,
        ref=ref,
        rows=rows,
        session=session,
        settings=settings,
        path=path,
        log=log,
        output=output,
        factory=factory,
    )


def read(h, **kwargs):
    return content.read_feedback_content(
        h.factory,
        h.settings,
        **{
            "owner_id": h.owner,
            "version_id": h.ref.version_id,
            "expected_sha256": h.ref.content_sha256,
            **kwargs,
        },
    )


def test_actual_file_read_after_release_reprojection_not_cached_text(retained):
    h = retained
    h.rows[ResearchInputRecord].disabled = True
    h.rows[ResearchInputRecord].current_version_id = uuid4()
    actual = read(h)
    assert actual.content == h.path.read_bytes()
    assert actual.sha256 == hashlib.sha256(actual.content).hexdigest()
    assert actual.size_bytes == len(actual.content) <= 32768
    assert actual.media_type == "application/json"
    assert actual.filename == f"feedback-input-{h.ref.version_id}.json"
    assert h.log == ["release-checked-before-source-bytes", "ledger-revalidated"]


@pytest.mark.parametrize(
    "fault",
    [
        "owner",
        "version",
        "sha",
        "origin",
        "inactive",
        "bytes",
        "object_owner",
        "object_size",
    ],
)
def test_content_rejects_foreign_changed_unreleased_or_wrong_profile(retained, fault):
    h = retained
    kwargs = {}
    if fault == "owner":
        h.rows[ResearchInputVersion].owner_id = uuid4()
    elif fault == "version":
        h.rows[ResearchInputVersion].id = uuid4()
    elif fault == "sha":
        kwargs["expected_sha256"] = "f" * 64
    elif fault == "origin":
        h.rows[ResearchInputRecord].origin = "normalized_external_proposal"
    elif fault == "inactive":
        h.rows[User].is_active = False
    elif fault == "bytes":
        h.path.write_bytes(b"changed")
    elif fault == "object_owner":
        h.rows[ResearchInputObject].owner_id = uuid4()
    elif fault == "object_size":
        h.rows[ResearchInputObject].size_bytes += 1
    with pytest.raises(DomainError):
        read(h, **kwargs)
    if fault in {"owner", "version", "sha", "origin", "inactive"}:
        assert not h.log


def test_release_required_before_any_feedback_bytes(retained, monkeypatch):
    def blocked(*args):
        raise DomainError("workflow_feedback_release_required", "Unavailable", 409)

    monkeypatch.setattr(resolver, "read_released_feedback_in_session", blocked)
    monkeypatch.setattr(
        resolver,
        "read_input_object",
        lambda *a, **k: pytest.fail("read before existing release"),
    )
    monkeypatch.setattr(
        content,
        "read_input_object",
        lambda *a, **k: pytest.fail("read before existing release"),
    )
    with pytest.raises(DomainError, match="Unavailable"):
        read(retained)


def test_actual_native_guard_is_mandatory(retained, monkeypatch):
    @contextmanager
    def no_guard(*args):
        yield retained.session

    monkeypatch.setattr(content, "historical_session", no_guard)
    with pytest.raises(DomainError):
        read(retained)
    assert not retained.log


def test_exact_reference_required_before_factory(retained, monkeypatch):
    monkeypatch.setattr(
        content,
        "historical_session",
        lambda *a: pytest.fail("invalid reference opened session"),
    )
    with pytest.raises(DomainError):
        read(retained, expected_sha256="A" * 64)


def test_feedback_http_uses_only_fixed_source_reader_and_private_bytes(
    http,
    monkeypatch,
):
    h = http
    identity = uuid4()
    raw = b'{"text":"<script>passive</script>"}'
    sha = hashlib.sha256(raw).hexdigest()
    h.app.state.session_factory = object()
    h.app.state.settings = object()

    def reader(factory, settings, **kwargs):
        assert factory is h.app.state.session_factory
        assert settings is h.app.state.settings
        assert kwargs == {
            "owner_id": h.owner.id,
            "version_id": identity,
            "expected_sha256": sha,
        }
        return NS(content=raw, sha256=sha, size_bytes=len(raw))

    monkeypatch.setattr(routes, "read_feedback_content", reader)
    monkeypatch.setattr(
        routes,
        "read_normalized_content",
        lambda *a, **k: pytest.fail("generic normalized bypass"),
    )
    response = h.client.get(
        f"/research-inputs/feedback-versions/{identity}/content?expected_sha256={sha}"
    )
    assert response.status_code == 200 and response.content == raw
    assert (
        response.headers["content-disposition"]
        == f'attachment; filename="feedback-input-{identity}.json"'
    )
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-content-sha256"] == sha
    assert response.headers["content-type"] == "application/json"


@pytest.mark.parametrize(
    "fault",
    [
        "missing_sha",
        "invalid_sha",
        "blocked",
        "too_large",
        "changed_sha",
        "changed_body",
    ],
)
def test_feedback_http_never_returns_partial_or_unqualified_bytes(
    http,
    monkeypatch,
    fault,
):
    http.app.state.session_factory = object()
    http.app.state.settings = object()

    def reader(*a, **kw):
        if fault in {"missing_sha", "invalid_sha"}:
            pytest.fail("invalid HTTP reference reached source")
        if fault == "blocked":
            raise DomainError(
                "workflow_feedback_release_required", "private diagnostics", 409
            )
        return NS(
            content=b"x" * 32769 if fault == "too_large" else b"x",
            sha256="b" * 64 if fault == "changed_sha" else "a" * 64,
            size_bytes=32769 if fault == "too_large" else 1,
        )

    monkeypatch.setattr(routes, "read_feedback_content", reader)
    suffix = (
        ""
        if fault == "missing_sha"
        else "?expected_sha256=" + ("A" * 64 if fault == "invalid_sha" else "a" * 64)
    )
    response = http.client.get(
        f"/research-inputs/feedback-versions/{uuid4()}/content{suffix}"
    )
    assert response.status_code == (
        422 if fault in {"missing_sha", "invalid_sha"} else 409
    )
    assert "private diagnostics" not in response.text
    assert "x-content-sha256" not in response.headers
