"""Authenticated result transport with explicit private-reader doubles, no PG claim."""

from uuid import uuid4

import pytest

from backend.app import oe_structured_result_routes as routes
from backend.app.services import DomainError
from tests.openevolve.test_structured_routes import api as api  # noqa: PLC0414


@pytest.fixture
def result_api(api):
    client, app, user, headers, _ = api
    app.include_router(routes.router, prefix="/api/v1")
    return client, app, user, headers


def test_release_passes_authenticated_owner_and_exact_execution_only(
    result_api, monkeypatch
):
    client, app, owner, headers = result_api
    run, execution = uuid4(), uuid4()
    exposures = (uuid4(), uuid4())
    calls = []

    def disclose(factory, settings, **kwargs):
        assert factory is app.state.session_factory
        assert settings is app.state.settings
        calls.append(kwargs)
        return (), exposures

    monkeypatch.setattr(routes, "disclose", disclose)
    response = client.post(
        f"/api/v1/engine-runs/structured-search/runs/{run}/executions/{execution}/release",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert calls == [
        {
            "owner_id": owner.id,
            "run_id": run,
            "execution_ids": (execution,),
            "create_release": True,
        }
    ]
    assert response.json()["exposure_ids"] == list(map(str, exposures))
    assert response.json()["qualification"] == "exposure_record_not_execution_authority"
    assert "measurement" not in response.json()


@pytest.mark.parametrize("method,suffix", [("get", ""), ("post", "/release")])
def test_missing_bearer_never_reads_or_releases(
    result_api, monkeypatch, method, suffix
):
    client, _, _, _ = result_api
    monkeypatch.setattr(
        routes, "disclose", lambda *a, **k: pytest.fail("No unauthorized read")
    )
    response = getattr(client, method)(
        f"/api/v1/engine-runs/structured-search/runs/{uuid4()}/executions/{uuid4()}{suffix}"
    )
    assert response.status_code == 401


def test_unreleased_read_does_not_project_private_values(result_api, monkeypatch):
    client, _, _, headers = result_api

    def denied(*a, **k):
        assert k["create_release"] is False
        raise DomainError(
            "engine_result_release_required", "Explicit release required", 409
        )

    monkeypatch.setattr(routes, "disclose", denied)
    monkeypatch.setattr(
        routes, "project", lambda *a: pytest.fail("No values before release")
    )
    response = client.get(
        f"/api/v1/engine-runs/structured-search/runs/{uuid4()}/executions/{uuid4()}",
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["code"] == "engine_result_release_required"


def test_release_commit_failure_returns_no_exposure_receipt(result_api, monkeypatch):
    client, _, _, headers = result_api

    def failed(*a, **k):
        raise DomainError("publication_failed", "Commit failed", 503)

    monkeypatch.setattr(routes, "disclose", failed)
    response = client.post(
        f"/api/v1/engine-runs/structured-search/runs/{uuid4()}/executions/{uuid4()}/release",
        headers=headers,
    )
    assert response.status_code == 503
    assert "exposure_ids" not in response.json()
