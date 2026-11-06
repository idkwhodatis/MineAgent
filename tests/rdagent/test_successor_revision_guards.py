"""Closed successor dispatch checks; these doubles are not PG/runtime acceptance."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app import rdagent_control_services as control
from backend.app.services import DomainError
from workers.research import rdagent_queue as queue


def revision_session(versions):
    def forbidden(*args, **kwargs):
        raise AssertionError(
            "Unknown schema must fail before reading or mutating a run"
        )

    return SimpleNamespace(
        scalars=lambda sql: SimpleNamespace(all=lambda: versions),
        scalar=forbidden,
        get=forbidden,
        add=forbidden,
    )


@pytest.mark.parametrize(
    "versions",
    [[], ["026_future"], ["023_rd_submissions", "025_structured_factor_strategy"]],
)
def test_unknown_revisions_do_not_claim_dispatch_or_cancel(versions, monkeypatch):
    session = revision_session(versions)
    with pytest.raises(DomainError, match="RD queue schema unavailable"):
        queue._claim(session, uuid4(), 1, "worker", None, None, None)
    monkeypatch.setattr(queue, "_queued_identity", lambda session, run: object())
    with pytest.raises(DomainError, match="RD queue schema unavailable"):
        queue.require_dispatch_binding(session, object(), object(), None)
    with pytest.raises(DomainError, match="RD control schema is unavailable"):
        control.request_cancel(session, uuid4(), uuid4())
    assert not control.controls_for(session, object()).cancel


@pytest.mark.parametrize(
    "revision",
    [
        "023_rd_submissions",
        "024_openevolve_initial",
        "025_structured_factor_strategy",
        "026_rd_seeded_submissions",
        "027_rd_controller_v3",
        "027a_contract_repairs",
        "027b_provider_material",
        "027c_seeded_controller_v4",
    ],
)
def test_successor_dispatch_reaches_exact_outbox_check_not_new_authority(
    revision, monkeypatch
):
    monkeypatch.setattr(queue, "_queued_identity", lambda session, run: object())
    run = SimpleNamespace(id=uuid4(), generation=1)
    event = SimpleNamespace(run_id=uuid4(), generation=1)
    with pytest.raises(DomainError) as exc:
        queue.require_dispatch_binding(revision_session([revision]), run, event, None)
    assert exc.value.code == "rd_dispatch_binding"


@pytest.mark.parametrize(
    "revision",
    [
        "023_rd_submissions",
        "024_openevolve_initial",
        "025_structured_factor_strategy",
        "026_rd_seeded_submissions",
        "027_rd_controller_v3",
        "027a_contract_repairs",
        "027b_provider_material",
        "027c_seeded_controller_v4",
    ],
)
def test_successor_control_still_requires_real_submission_receipt(revision):
    session = revision_session([revision])
    session.scalar = lambda query: None
    run = SimpleNamespace(id=uuid4(), owner_id=uuid4(), status="running")
    assert not control.controls_for(session, run).cancel


def test_reconciler_preserves_exact_rd_stalled_job_dispatch(monkeypatch):
    from contextlib import nullcontext
    from unittest.mock import MagicMock

    from workers.research import __main__ as main

    session = MagicMock()
    session.scalars.return_value.all.return_value = []
    factory = SimpleNamespace(begin=lambda: nullcontext(session))
    settings = SimpleNamespace(
        factor_library_enabled=False, factor_import_enabled=False
    )
    monkeypatch.setattr("sys.argv", ["research", "reconciler", "--once"])
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "create_db_engine", lambda _: object())
    monkeypatch.setattr(main, "session_factory", lambda _: factory)
    recover = MagicMock(return_value=0)
    monkeypatch.setattr(main, "recover_stalled_research_jobs", recover)
    main.main()
    recover.assert_called_once_with(session)


@pytest.mark.parametrize(
    "revision",
    [
        "021_execution_snapshots",
        "022_factor_analysis",
        "023_rd_submissions",
        "024_openevolve_initial",
        "025_structured_factor_strategy",
        "026_rd_seeded_submissions",
        "027_rd_controller_v3",
        "027a_contract_repairs",
        "027b_provider_material",
        "027c_seeded_controller_v4",
    ],
)
def test_original_maintained_fixture_remains_supported_on_explicit_successors(revision):
    from workers.research.rdagent_operational import require_maintained_revision

    require_maintained_revision(revision_session([revision]))


@pytest.mark.parametrize(
    "versions",
    [[], ["026_future"], ["021_execution_snapshots", "025_structured_factor_strategy"]],
)
def test_original_maintained_fixture_never_infers_unknown_compatibility(versions):
    from packages.research_runtime.process_contracts import ProcessError
    from workers.research.rdagent_operational import require_maintained_revision

    with pytest.raises(ProcessError, match="rd_controller_revision"):
        require_maintained_revision(revision_session(versions))
