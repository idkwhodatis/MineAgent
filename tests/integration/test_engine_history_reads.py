"""Actual migrated PostgreSQL + authenticated HTTP; never mock persisted RD rows."""

import pytest
from rd_source_fixture import persist_maintained_source
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.postgres


def test_rd_history_actual_owner_list_detail_source_and_reload(
    migrated_engine,
    settings,
    api,
    owner,
    owner_headers,
    other_headers,
):
    saved = persist_maintained_source(
        sessionmaker(migrated_engine, expire_on_commit=False),
        owner=owner.user.id,
        dataset=owner.dataset,
        settings=settings,
        source_utf8=b"# Persisted history fixture; not executed\ndef factor():\n    return 1\n",
    )
    path = f"/api/v1/engine-runs/{saved.fence.run_id}"
    listed = api.get("/api/v1/engine-runs", headers=owner_headers)
    assert listed.status_code == 200
    assert [r["id"] for r in listed.json()["data"]] == [str(saved.fence.run_id)]
    detail = api.get(path, headers=owner_headers)
    assert detail.status_code == 200
    assert detail.json()["strategy_version_id"] is None
    assert detail.json()["engine"] == "rdagent"
    assert detail.json()["status"] == "running"  # Storage fixture is not success.
    assert detail.json()["actions"]["resume"] is False
    first = api.get(path + "/history", headers=owner_headers)
    assert first.status_code == 200
    row = first.json()["data"][0]
    assert row["source_id"] == str(saved.source.id)
    assert row["factor_version_id"] == str(saved.version.id)
    assert row["source_sha256"] == saved.source.source_sha256
    assert row["snapshot_id"] is None
    assert row["evaluation_status"] is None
    assert api.get(path + "/history", headers=owner_headers).json() == first.json()
    for suffix in ["", "/history"]:
        assert api.get(path + suffix, headers=other_headers).status_code == 404
        assert api.get(path + suffix).status_code == 401
    assert api.get("/api/v1/engine-runs", headers=other_headers).json()["data"] == []
    assert (
        api.get(path + "/history?offset=-1", headers=owner_headers).status_code == 422
    )


def test_engine_public_entry_remains_closed_and_legacy_detail_is_not_reinterpreted(
    api,
    owner_headers,
    queued_run,
    owner,
):
    capability = api.get("/api/v1/engine-runs/capabilities", headers=owner_headers)
    assert capability.status_code == 200
    assert all(not item["can_create"] for item in capability.json()["engines"])
    assert capability.json()["engines"][1]["can_read_history"] is False
    before = api.get("/api/v1/engine-runs", headers=owner_headers).json()
    for body in [{}, {"engine": "rdagent"}, {"engine": "openevolve"}]:
        assert (
            api.post(
                "/api/v1/engine-runs", headers=owner_headers, json=body
            ).status_code
            == 422
        )
    from rd_submission_fixture import body as submission_body

    from packages.rdagent_adapter.configured_source import ConfiguredSourcePolicy

    request = submission_body(
        owner.dataset,
        ConfiguredSourcePolicy(
            acknowledged_unverified=True,
        ),
    )
    assert (
        api.post(
            "/api/v1/engine-runs",
            headers={**owner_headers, "Idempotency-Key": "closed-profile"},
            json={**request.model_dump(mode="json"), "preflight_sha256": "a" * 64},
        ).status_code
        == 409
    )
    assert api.get("/api/v1/engine-runs", headers=owner_headers).json() == before
    assert (
        api.get(
            f"/api/v1/engine-runs/{queued_run['id']}", headers=owner_headers
        ).status_code
        == 404
    )
