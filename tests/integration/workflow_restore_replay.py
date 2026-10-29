"""Same actual028 fixture cold roundtrip; no new child/provider execution units."""


def _paths(factory, owner_id, workflow_id):
    """Bounded actual retained identities, not a fabricated expected result."""
    from sqlalchemy import select

    from backend.app.research_input_models import (
        ResearchInputDerivation,
        ResearchInputObject,
        ResearchInputRecord,
        ResearchInputVersion,
    )
    from backend.app.research_workflow_models import ResearchWorkflowStage

    base = f"/api/v1/research-workflows/{workflow_id}"
    paths = [base, base + "/selected-inputs", base + "/stages?limit=32"]
    with factory() as session:
        stages = tuple(
            session.scalars(
                select(ResearchWorkflowStage)
                .filter_by(owner_id=owner_id, workflow_id=workflow_id)
                .order_by(ResearchWorkflowStage.ordinal)
                .limit(18)
            )
        )
        assert 1 <= len(stages) <= 17
        for stage in stages:
            paths.append(base + f"/stages/{stage.id}")
            if stage.child_run_id is not None:
                paths.append(f"/api/v1/engine-runs/{stage.child_run_id}/inputs")
                paths.append(f"/api/v1/engine-runs/{stage.child_run_id}/history")
        derivations = tuple(
            session.scalars(
                select(ResearchInputDerivation)
                .filter_by(owner_id=owner_id, workflow_id=workflow_id)
                .limit(18)
            )
        )
        assert len(derivations) <= 17
        ids = [item.id for item in derivations]
        raw_ids = {item.raw_object_id for item in derivations if item.raw_object_id}
        for object_id in sorted(raw_ids, key=str):
            obj = session.scalar(
                select(ResearchInputObject).filter_by(owner_id=owner_id, id=object_id)
            )
            assert obj is not None and obj.kind == "raw_external"
            paths.append(
                f"/api/v1/research-inputs/external-objects/{obj.id}/content"
                f"?expected_sha256={obj.sha256}"
            )
        versions = tuple(
            session.execute(
                select(ResearchInputVersion, ResearchInputRecord.origin)
                .join(
                    ResearchInputRecord,
                    ResearchInputVersion.record_id == ResearchInputRecord.id,
                )
                .where(
                    ResearchInputVersion.owner_id == owner_id,
                    ResearchInputRecord.owner_id == owner_id,
                    ResearchInputVersion.derivation_id.in_(ids),
                )
                .order_by(ResearchInputVersion.id)
                .limit(65)
            )
        )
        assert len(versions) <= 64
        for version, origin in versions:
            assert origin in {
                "normalized_external_proposal",
                "experiment_feedback_proposal",
            }
            segment = (
                "feedback-versions"
                if origin == "experiment_feedback_proposal"
                else "versions"
            )
            paths.append(
                f"/api/v1/research-inputs/{segment}/{version.id}/content"
                f"?expected_sha256={version.content_sha256}"
            )
    assert len(paths) == len(set(paths)) and len(paths) <= 128
    return tuple(paths)


def assert_workflow_restore(
    drill_databases, tmp_path, source, settings, *, account, other, workflow_id
):
    """Actual pg_dump/restore plus source-qualified GETs, with operators disabled."""
    import test_backup_restore as legacy
    from fastapi.testclient import TestClient
    from oe_structured_restore_replay import _disabled_settings, _inventory
    from rd_submission_fixture import login
    from sqlalchemy.orm import sessionmaker
    from test_factor_restore import _files
    from test_neutral_restore import _roundtrip

    from backend.app.main import create_app
    from backend.app.operations_backup import BackupLimits, PostgresDatabase

    assert source.url == drill_databases.source.set(
        port=drill_databases.source.port or 5432
    )
    factory = sessionmaker(source, expire_on_commit=False)
    inventory = _inventory(factory)
    paths = _paths(factory, account.user.id, workflow_id)
    buffers = {}
    with TestClient(create_app(settings=settings, engine=source)) as api:
        headers, foreign = login(api, account), login(api, other)
        for path in paths:
            response = api.get(path, headers=headers)
            assert response.status_code == 200, (path, response.text)
            buffers[path] = (response.headers["content-type"], response.content)
            assert api.get(path, headers=foreign).status_code == 404, path
    assert _inventory(factory) == inventory
    before = PostgresDatabase(
        source.url.render_as_string(hide_password=False)
    ).source_snapshot(BackupLimits())
    files = _files(settings.storage_root)
    target = tmp_path / "workflow-restored-storage"
    manifest = _roundtrip(
        drill_databases, tmp_path, source, settings.storage_root, target
    )
    assert manifest["schema_revision"] == "029_research_workflow"
    assert target.stat().st_ino != settings.storage_root.stat().st_ino
    assert not (target / ".restore-incomplete").exists()
    assert _files(target) == files
    restored = legacy._engine(drill_databases.restored)
    try:
        restored_settings = _disabled_settings(settings, restored, target)
        restored_factory = sessionmaker(restored, expire_on_commit=False)
        after = PostgresDatabase(
            restored.url.render_as_string(hide_password=False)
        ).source_snapshot(BackupLimits())
        keys = {key for key in before if key.endswith("_closure")}
        assert {
            "workflow_closure",
            "research_input_closure",
            "research_closure",
            "rd_source_closure",
        } <= keys
        assert keys == {key for key in after if key.endswith("_closure")}
        for key in keys:
            assert after[key] == before[key], key
        assert _inventory(restored_factory) == inventory
        assert _paths(restored_factory, account.user.id, workflow_id) == paths
        with TestClient(create_app(settings=restored_settings, engine=restored)) as api:
            headers, foreign = login(api, account), login(api, other)
            for path in paths:
                response = api.get(path, headers=headers)
                assert response.status_code == 200, (path, response.text)
                assert (response.headers["content-type"], response.content) == buffers[
                    path
                ], path
                assert api.get(path, headers=foreign).status_code == 404, path
        # No release POST, tick, queue, launch or repair is needed to replay reads.
        assert _inventory(restored_factory) == inventory
        final = PostgresDatabase(
            restored.url.render_as_string(hide_password=False)
        ).source_snapshot(BackupLimits())
        for key in keys:
            assert final[key] == before[key], key
        assert _files(target) == files
    finally:
        restored.dispose()
