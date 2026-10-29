"""Notes over the existing actual workflow result; no extra engine/model calls.

This helper must run after the original exact031 restore assertions. HTTP writes
are explicit user note/release actions, never fabricated execution or ledger rows.
"""

from copy import deepcopy
from dataclasses import dataclass

from packages.factors.contracts import canonical_bytes, digest


@dataclass(frozen=True)
class NormalNoteReads:
    responses: tuple[tuple[str, bytes], ...]
    exports: tuple[tuple[str, bytes], ...]
    capture_paths: tuple[str, ...]


def capture_path(run_id, execution_id, kind, selector=""):
    return (
        f"/api/v1/research-note-evidence/normal-oe/{run_id}/{execution_id}"
        f"?kind={kind}{selector}"
    )


def assert_unavailable_before032(env, completed):
    path = capture_path(
        completed.oe_run_id, completed.execution_ids[0], "normal_oe_execution"
    )
    response = env.client.get(path, headers=env.headers)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "normal_oe_notes_not_installed"


def create_released_notes(env, completed):
    """Capture all three typed identities and two immutable note revisions."""
    from urllib.parse import urlencode

    execution_id = completed.execution_ids[0]
    result_path = (
        f"/api/v1/engine-runs/structured-search/runs/{completed.oe_run_id}"
        f"/executions/{execution_id}"
    )
    first_path = capture_path(completed.oe_run_id, execution_id, "normal_oe_execution")
    blocked = env.client.get(first_path, headers=env.headers)
    assert blocked.status_code == 409, blocked.text
    assert env.client.get(first_path, headers=env.foreign).status_code == 404
    assert (
        env.client.post(result_path + "/release", headers=env.foreign).status_code
        == 404
    )
    released = env.client.post(result_path + "/release", headers=env.headers)
    assert released.status_code == 200, released.text
    assert len(released.json()["exposure_ids"]) == 2
    result = env.client.get(result_path, headers=env.headers)
    assert result.status_code == 200, result.text
    view = result.json()["execution"]
    # Require observed decisions and fills, not synthetic note-only row fixtures.
    assert view["decisions"] and view["fills"]
    decision, fill = view["decisions"][0], view["fills"][0]
    paths = (
        first_path,
        capture_path(
            completed.oe_run_id,
            execution_id,
            "normal_oe_decision",
            "&" + urlencode({"decision_sequence": decision["event_seq"]}),
        ),
        capture_path(
            completed.oe_run_id,
            execution_id,
            "normal_oe_fill",
            "&" + urlencode({"native_fill_id": fill["native_fill_id"]}),
        ),
    )
    references, hrefs, responses = (
        [],
        [],
        [(result_path, canonical_bytes(result.json()))],
    )
    for path in paths:
        response = env.client.get(path, headers=env.headers)
        assert response.status_code == 200, response.text
        value = response.json()
        assert value["qualification"] == "released_original_identity_not_note_write"
        assert value["evidence"]["execution_id"] == str(execution_id)
        assert value["evidence"]["run_id"] == str(completed.oe_run_id)
        assert env.client.get(path, headers=env.foreign).status_code == 404
        references.append(value["evidence"])
        hrefs.append(value["href"])
        responses.append((path, canonical_bytes(value)))
    assert references[1]["row_sha256"] == digest(decision)
    assert references[2]["row_sha256"] == digest(fill)
    body = {
        "content": {
            "title": "Observed normal OE execution, decision and fill",
            "body": "Descriptive evidence only; not causal gain or trading advice.",
            "evidence": references,
            "restore": {
                "schema_version": "mineagent.note-normal-oe-restore.v1",
                "evidence_index": 2,
            },
        }
    }
    endpoint = "/api/v1/research-notes"
    headers = {**env.headers, "Idempotency-Key": "actual032-normal-note"}
    created = env.client.post(endpoint, headers=headers, json=body)
    assert created.status_code == 201, created.text
    first = created.json()
    assert first["retained_pin_count"] == 1  # Three selectors, one actual execution.
    assert first["restore_href"] == hrefs[2]
    replay = env.client.post(endpoint, headers=headers, json=body)
    assert replay.status_code == 201, replay.text
    assert canonical_bytes(replay.json()) == canonical_bytes(first)
    tampered = deepcopy(body)
    tampered["content"]["evidence"][1]["row_sha256"] = "0" * 64
    denied = env.client.post(
        endpoint,
        headers={**env.headers, "Idempotency-Key": "actual032-tampered"},
        json=tampered,
    )
    assert denied.status_code == 409, denied.text
    edit = deepcopy(body)
    edit.update(
        base_revision=first["revision"], base_revision_sha256=first["revision_sha256"]
    )
    edit["content"]["body"] += " Human revision preserves original evidence."
    edit["content"]["restore"]["evidence_index"] = 1
    note_path = endpoint + "/" + first["note_id"]
    edited = env.client.post(
        note_path + "/revisions",
        headers={**env.headers, "Idempotency-Key": "actual032-normal-note-edit"},
        json=edit,
    )
    assert edited.status_code == 201, edited.text
    second = edited.json()
    assert second["revision"] == 2 and second["restore_href"] == hrefs[1]
    stale = env.client.post(
        note_path + "/revisions",
        headers={**env.headers, "Idempotency-Key": "actual032-stale-edit"},
        json=edit,
    )
    assert stale.status_code == 409, stale.text
    exports = []
    for revision in (first, second):
        path = note_path + f"/revisions/{revision['revision']}"
        query = "?revision_sha256=" + revision["revision_sha256"]
        response = env.client.get(path + query, headers=env.headers)
        assert response.status_code == 200, response.text
        assert canonical_bytes(response.json()) == canonical_bytes(revision)
        assert env.client.get(path + query, headers=env.foreign).status_code == 404
        responses.append((path + query, canonical_bytes(revision)))
        exported = env.client.get(path + "/export" + query, headers=env.headers)
        assert exported.status_code == 200, exported.text
        exports.append((path + "/export" + query, exported.content))
    saved = NormalNoteReads(tuple(responses), tuple(exports), paths)
    from oe_structured_browser import assert_normal_oe_notes_in_browser

    browser_revisions = assert_normal_oe_notes_in_browser(
        env.factory.kw["bind"],
        env.settings,
        env.account,
        completed.oe_run_id,
        execution_id,
        env.other,
        saved,
        edit=True,
    )
    for item in browser_revisions:
        path = f"/api/v1/research-notes/{item['note_id']}/revisions/{item['revision']}"
        query = "?revision_sha256=" + item["revision_sha256"]
        response = env.client.get(path + query, headers=env.headers)
        assert response.status_code == 200, response.text
        revision = response.json()
        assert revision["revision_sha256"] == item["revision_sha256"]
        assert revision["content"]["evidence"] == [references[2]]
        assert revision["restore_href"] == hrefs[2]
        assert env.client.get(path + query, headers=env.foreign).status_code == 404
        responses.append((path + query, canonical_bytes(revision)))
        exported = env.client.get(path + "/export" + query, headers=env.headers)
        assert exported.status_code == 200, exported.text
        exports.append((path + "/export" + query, exported.content))
    return NormalNoteReads(tuple(responses), tuple(exports), paths)


def assert_note_reads(client, headers, foreign, saved):
    """GET-only restored proof, including old revision and exact exports."""
    for path, raw in saved.responses:
        response = client.get(path, headers=headers)
        assert response.status_code == 200, (path, response.text)
        assert canonical_bytes(response.json()) == raw
        assert client.get(path, headers=foreign).status_code == 404
    for path, raw in saved.exports:
        response = client.get(path, headers=headers)
        assert response.status_code == 200, (path, response.text)
        assert response.content == raw
        assert client.get(path, headers=foreign).status_code == 404


def assert_notes_restore(databases, tmp_path, source, env, completed, saved):
    """Use a fresh restore database; never overwrite the prior031 restored DB."""
    import test_backup_restore as legacy
    from fastapi.testclient import TestClient
    from oe_structured_restore_replay import _disabled_settings, _inventory
    from rd_submission_fixture import login
    from sqlalchemy.orm import sessionmaker
    from test_factor_restore import _files
    from test_neutral_restore import _roundtrip

    from backend.app.main import create_app
    from backend.app.operations_backup import BackupLimits, PostgresDatabase

    assert databases.source == source.url
    original = PostgresDatabase(
        source.url.render_as_string(hide_password=False)
    ).source_snapshot(BackupLimits())
    assert original["normal_oe_note_closure"]["schema_version"] == (
        "mineagent.note-normal-oe-closure.v1"
    )
    for key, schema in (
        ("research_closure", "mineagent.research-restore-closure.v16"),
        ("rd_source_closure", "mineagent.rd-source-restore-closure.v11"),
        ("workflow_closure", "mineagent.research-workflow-restore-closure.v2"),
        ("oe_structured_closure", "mineagent.oe-structured-restore-closure.v2"),
    ):
        assert original[key]["schema_version"] == schema
    before_files = _files(env.settings.storage_root)
    before_inventory = _inventory(env.factory)
    target = tmp_path / "normal-notes-restored"
    manifest = _roundtrip(
        databases, tmp_path, source, env.settings.storage_root, target
    )
    assert manifest["schema_revision"] == "032_normal_oe_note_pins"
    assert _files(target) == before_files
    restored = legacy._engine(databases.restored)
    try:
        settings = _disabled_settings(env.settings, restored, target)
        factory = sessionmaker(restored, expire_on_commit=False)
        after = PostgresDatabase(
            restored.url.render_as_string(hide_password=False)
        ).source_snapshot(BackupLimits())
        closures = {key for key in original if key.endswith("_closure")}
        assert closures == {key for key in after if key.endswith("_closure")}
        assert all(original[key] == after[key] for key in closures)
        assert _inventory(factory) == before_inventory
        with TestClient(create_app(settings=settings, engine=restored)) as client:
            headers, foreign = login(client, env.account), login(client, env.other)
            assert_note_reads(client, headers, foreign, saved)
        from oe_structured_browser import assert_normal_oe_notes_in_browser

        assert_normal_oe_notes_in_browser(
            restored,
            settings,
            env.account,
            completed.oe_run_id,
            completed.execution_ids[0],
            env.other,
            saved,
        )
        # Historical read/export/capture must not append either domain or note rows.
        assert _inventory(factory) == before_inventory
        reread = PostgresDatabase(
            restored.url.render_as_string(hide_password=False)
        ).source_snapshot(BackupLimits())
        assert all(after[key] == reread[key] for key in closures)
        assert _files(target) == before_files
    finally:
        restored.dispose()
