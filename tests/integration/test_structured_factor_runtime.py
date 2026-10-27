"""Required actual factor→closed operator→AKQuant pair; no local execution claim."""

import os

import pytest

pytestmark = pytest.mark.postgres


def test_actual_two_factor_versions_drive_two_closed_engine_units(
    migrated_engine, settings, owner, other, tmp_path, monkeypatch
):
    if os.environ.get("MINEAGENT_STRUCTURED_ENGINE_REQUIRED") != "1":
        pytest.skip("Explicit actual structured engine gate was not requested")
    from structured_execution_fixture import run_structured_pair

    from backend.app.structured_factor_history import (
        read_historical_structured_execution,
    )
    from workers.research.structured_process import execute_structured_acceptance_unit

    factory, fence, execution_ids, outputs = run_structured_pair(
        migrated_engine, settings, owner.user.id
    )
    # Actual retained-tree relocation, not a pg_dump/restore claim. Historical
    # projection has no old-fence capability and must not enable another unit.
    import shutil
    from uuid import uuid4

    from backend.app.services import DomainError
    from packages.research_runtime.process_contracts import ProcessError
    from runtimes.research_supervisor.structured_factor_supervisor import (
        StructuredSupervisor,
    )

    relocated = tmp_path / "relocated-structured"
    shutil.copytree(settings.storage_root, relocated)
    assert relocated.stat().st_ino != settings.storage_root.stat().st_ino
    restored_settings = settings.model_copy(update={"storage_root": relocated})
    for expected in outputs:
        historical = read_historical_structured_execution(
            factory, owner.user.id, expected.execution_id, restored_settings
        )
        assert historical.result == expected.result
        with pytest.raises(DomainError):
            read_historical_structured_execution(
                factory, uuid4(), expected.execution_id, restored_settings
            )
    attempted = []

    def no_launch(*args, **kwargs):
        attempted.append(True)
        raise AssertionError("Historical execution must never reach Docker launch")

    monkeypatch.setattr(StructuredSupervisor, "execute", no_launch)
    with pytest.raises((DomainError, ProcessError)):
        execute_structured_acceptance_unit(
            factory, fence, execution_ids[0], restored_settings
        )
    assert attempted == []

    # The genuine browser is the first public disclosure of both executions.
    # Existing stopped units are reused; this must not launch another process.
    from structured_result_browser import assert_actual_structured_result_browser

    assert_actual_structured_result_browser(
        migrated_engine,
        restored_settings,
        owner,
        other,
        factory,
        fence.run_id,
        execution_ids,
        tmp_path,
    )
    assert attempted == []
