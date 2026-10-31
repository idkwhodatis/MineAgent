"""Actual same-fixture normal027 backup/restore replay; no new execution units.

The enclosing required user-flow supplies already disclosed response buffers.
This is deliberately not an independent pytest node or a persistence initializer.
"""


def _disabled_settings(settings, restored, target):
    return settings.model_copy(
        update={
            "database_url": restored.url.render_as_string(hide_password=False),
            "storage_root": target,
            "model_profiles_enabled": False,
            "model_profiles": (),
            "model_synthetic_test_mode": False,
            "oe_structured_research_enabled": False,
            "oe_structured_runtime_profile": None,
            "oe_structured_provider_profiles": (),
            "oe_structured_live_provider_enabled": False,
            "oe_structured_synthetic_provider_enabled": False,
            "oe_structured_provider_python": None,
            "rdagent_research_enabled": False,
            "rdagent_runtime_profile": None,
            "rdagent_seeded_runtime_profile": None,
            "rdagent_provider_runtime_profile": None,
            "rdagent_provider_profiles": (),
            "rdagent_live_provider_enabled": False,
            "rdagent_synthetic_provider_enabled": False,
            "rdagent_provider_python": None,
            "openevolve_research_enabled": False,
        }
    )


def _inventory(factory):
    from sqlalchemy import select

    from backend.app.models import DataUsageExposure, ResearchRun
    from backend.app.research_broker_models import ResearchModelOperation
    from backend.app.research_process_models import ResearchProcessAdmission

    with factory() as session:
        return tuple(
            frozenset(session.scalars(select(model.id)))
            for model in (
                ResearchRun,
                ResearchProcessAdmission,
                ResearchModelOperation,
                DataUsageExposure,
            )
        )


def assert_normal_restore(
    drill_databases,
    tmp_path,
    source,
    settings,
    *,
    account,
    other,
    run_id,
    execution_ids,
    responses,
    receipts,
    expected_revision="028_oe_structured_search",
):
    """Real pg_dump/restore, private bytes and public GET-before-POST equality."""
    import pytest
    import test_backup_restore as legacy
    from fastapi.testclient import TestClient
    from rd_submission_fixture import login
    from sqlalchemy.orm import sessionmaker
    from test_factor_restore import _files
    from test_neutral_restore import _roundtrip

    from backend.app.main import create_app
    from backend.app.oe_structured_result_history import read_result
    from backend.app.operations_backup import BackupLimits, PostgresDatabase
    from backend.app.services import DomainError
    from packages.factors.contracts import canonical_bytes

    assert expected_revision in {
        "028_oe_structured_search",
        "030_declared_upload_basket",
        "032a_rd_terminal_results",
    }
    ids = tuple(execution_ids)
    assert ids and len(ids) == len(set(ids))
    assert responses and receipts
    # The fixture owns the source database; never back up some other connection.
    assert source.url == drill_databases.source.set(
        port=drill_databases.source.port or 5432
    )
    factory = sessionmaker(source, expire_on_commit=False)
    originals = tuple(
        read_result(factory, settings, owner_id=account.user.id, execution_id=eid)
        for eid in ids
    )
    assert all(value.execution.run_id == run_id for value in originals)
    before_inventory = _inventory(factory)
    source_db = PostgresDatabase(source.url.render_as_string(hide_password=False))
    before = source_db.source_snapshot(BackupLimits())
    files = _files(settings.storage_root)
    target = tmp_path / "normal-restored-storage"
    manifest = _roundtrip(
        drill_databases, tmp_path, source, settings.storage_root, target
    )
    assert manifest["schema_revision"] == expected_revision
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
        assert {"oe_structured_closure", "research_closure"} <= keys
        if expected_revision in {
            "030_declared_upload_basket",
            "032a_rd_terminal_results",
        }:
            assert "declared_upload_closure" in keys
        assert keys == {key for key in after if key.endswith("_closure")}
        for key in keys:
            assert after[key] == before[key]
        assert _inventory(restored_factory) == before_inventory
        for eid, original in zip(ids, originals, strict=True):
            assert (
                read_result(
                    restored_factory,
                    restored_settings,
                    owner_id=account.user.id,
                    execution_id=eid,
                )
                == original
            )
            with pytest.raises(DomainError) as denied:
                read_result(
                    restored_factory,
                    restored_settings,
                    owner_id=other.user.id,
                    execution_id=eid,
                )
            assert (
                denied.value.status_code == 409
            )  # Private integrity reader, not public lookup.
        with TestClient(create_app(settings=restored_settings, engine=restored)) as api:
            headers, foreign = login(api, account), login(api, other)
            # Existing release rows must work before any replay POST.
            for path, raw in responses:
                response = api.get(path, headers=headers)
                assert response.status_code == 200, response.text
                assert canonical_bytes(response.json()) == raw
                assert api.get(path, headers=foreign).status_code == 404
            assert _inventory(restored_factory) == before_inventory
            for path, raw in receipts:
                response = api.post(path, headers=headers)
                assert response.status_code == 200, response.text
                assert canonical_bytes(response.json()) == raw
                assert api.post(path, headers=foreign).status_code == 404
        assert _inventory(restored_factory) == before_inventory
    finally:
        restored.dispose()
