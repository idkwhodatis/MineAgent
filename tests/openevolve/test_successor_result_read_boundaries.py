"""Successor reader gates with real codecs/capabilities and explicit DB doubles.

These test admission boundaries, not complete physical history or PostgreSQL
constraints. No successful result, release receipt or measurement is fabricated.
"""

from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from backend.app import oe_structured_operations as operations
from backend.app import oe_structured_result_history as history
from backend.app import oe_structured_result_release as release
from backend.app import rdagent_snapshot_config as dispatch
from backend.app.schema_capabilities import capabilities_for
from backend.app.services import DomainError
from packages.factors.contracts import digest
from tests.openevolve.test_structured_persisted_identity import graph

REVISIONS = (
    "028_oe_structured_search",
    "029_research_workflow",
    "030_declared_upload_basket",
    "031_workflow_structured_stage",
)


@pytest.mark.parametrize(
    "revision,workflow,declared",
    [
        ("027_rd_controller_v3", False, False),
        (REVISIONS[0], False, False),
        (REVISIONS[1], True, False),
        (REVISIONS[2], True, True),
        (REVISIONS[3], True, True),
    ],
)
def test_actual_schema_queries_select_only_explicit_capabilities(
    revision, workflow, declared
):
    engine = create_engine("sqlite://")
    try:
        with Session(engine) as session:
            session.execute(text("CREATE TABLE alembic_version(version_num TEXT)"))
            session.execute(
                text("INSERT INTO alembic_version VALUES (:v)"), {"v": revision}
            )
            assert dispatch.provider_config_in_session(session) is True
            assert dispatch.composed_controller_in_session(session) is True
            assert dispatch.workflow_config_in_session(session) is workflow
            assert dispatch.declared_upload_in_session(session) is declared
    finally:
        engine.dispose()


@pytest.mark.parametrize("versions", [[], ["031_future"], [REVISIONS[2], REVISIONS[2]]])
def test_unknown_or_multiple_revisions_never_infer_successor_flags(versions):
    session = NS(scalars=lambda _: NS(all=lambda: versions))
    for helper in (
        dispatch.provider_config_in_session,
        dispatch.composed_controller_in_session,
        dispatch.workflow_config_in_session,
    ):
        with pytest.raises(ValueError):
            helper(session)
    assert dispatch.declared_upload_in_session(session) is False


@pytest.mark.parametrize("revision", REVISIONS)
@pytest.mark.parametrize("source", (7, 8, 9, 10, 11, 12))
def test_research_and_source_pair_is_exact_not_version_order(revision, source):
    caps = capabilities_for(revision)
    candidate = replace(
        caps, rd_source_closure_schema=f"mineagent.rd-source-restore-closure.v{source}"
    )
    expected = REVISIONS.index(revision) + 8
    closure = {"schema_version": caps.oe_structured_closure_schema, "tables": {}}
    if source == expected:
        assert (
            operations.closure_for({"oe_structured_closure": closure}, candidate)
            is closure
        )
    else:
        with pytest.raises(ValueError, match="capability pair differs"):
            operations.closure_for({"oe_structured_closure": closure}, candidate)


def release_graph(revisions):
    original, run, spec, _version, _candidate, _rows = graph()
    run.status = "succeeded"
    execution = NS(id=uuid4(), owner_id=run.owner_id, run_id=run.id)
    values = iter((run, execution))
    session = NS(
        get=original.get,
        scalar=lambda _: next(values),
        scalars=lambda _: NS(all=lambda: revisions),
    )
    return session, run, execution, spec


@pytest.mark.parametrize("revision", REVISIONS)
def test_release_gate_decodes_real_frozen_config_on_each_successor(revision):
    session, run, execution, spec = release_graph([revision])
    actual_run, actual_execution, config = release.owned_execution(
        session, run.owner_id, run.id, execution.id
    )
    assert actual_run is run and actual_execution is execution
    assert digest(config) == spec.config_sha256
    assert config.fixed_inputs.market_qualification == "synthetic_by_construction"


@pytest.mark.parametrize("revision", REVISIONS)
@pytest.mark.parametrize(
    "change", ["owner", "run", "execution", "active", "config_hash"]
)
def test_successor_revision_does_not_replace_owner_terminal_or_frozen_guards(
    revision, change
):
    session, run, execution, _ = release_graph([revision])
    owner_id, run_id, execution_id = run.owner_id, run.id, execution.id
    if change == "owner":
        execution.owner_id = uuid4()
    elif change == "run":
        execution.run_id = uuid4()
    elif change == "execution":
        execution.id = uuid4()
    elif change == "active":
        run.status = "running"
    else:
        run.request_sha256 = "0" * 64
    with pytest.raises(DomainError):
        release.owned_execution(session, owner_id, run_id, execution_id)


@pytest.mark.parametrize(
    "versions", [[], ["027_rd_controller_v3"], ["031_future"], list(REVISIONS)]
)
def test_release_unknown_revision_rejects_before_rows(versions):
    def forbidden(_):
        pytest.fail("rows loaded before exact schema gate")

    session = NS(scalars=lambda _: NS(all=lambda: versions), scalar=forbidden)
    with pytest.raises(DomainError):
        release.owned_execution(session, uuid4(), uuid4(), uuid4())


@pytest.mark.parametrize("revision", REVISIONS)
def test_history_passes_actual_successor_research_version_to_scoped_loader(
    monkeypatch, tmp_path, revision
):
    from backend.app import execution_snapshot_history as snapshots
    from backend.app import oe_structured_provider_containment as containment

    original, run, _spec, _, _, _ = graph()
    run.status = "succeeded"
    attempt = NS(id=uuid4(), generation=1, finished_at="retained", sandbox_stopped=True)
    execution = NS(id=uuid4(), run_id=run.id, attempt_id=attempt.id, generation=1)
    values = iter((execution, run))

    class DB:
        get = staticmethod(original.get)

        def scalars(self, statement):
            if "alembic_version" in str(statement):
                return NS(all=lambda: [revision])
            return [attempt]

        def scalar(self, _):
            return next(values)

    from backend.app.factor_native_io import native_storage_guard

    (tmp_path / "factors").mkdir()

    @contextmanager
    def session(*_):
        with native_storage_guard(tmp_path):
            yield DB()

    class ReachedScopedLoader(Exception):
        pass

    observed = []

    def loader(session, owner, loaded_run, config, *, research_schema):
        observed.append(research_schema)
        assert owner == run.owner_id and loaded_run is run
        assert config.search.run_id == run.id
        raise ReachedScopedLoader

    monkeypatch.setattr(snapshots, "historical_session", session)
    monkeypatch.setattr(containment, "provider_attempt_stopped", lambda *_: True)
    monkeypatch.setattr(history, "_load", loader)
    # Deliberate sentinel: proving dispatch only, not returning a fake valid history.
    with pytest.raises(ReachedScopedLoader):
        history.read_result(
            object(),
            NS(storage_root=tmp_path),
            owner_id=run.owner_id,
            execution_id=execution.id,
        )
    assert observed == [capabilities_for(revision).research_closure_schema]


@pytest.mark.parametrize(
    "revision",
    ("027_rd_controller_v3", "027a_contract_repairs", "027b_provider_material")
    + REVISIONS,
)
@pytest.mark.parametrize("drift", [False, True])
@pytest.mark.parametrize("composed", [False, True])
def test_nonempty_configured_witness_is_revalidated_not_skipped(
    monkeypatch, tmp_path, revision, drift, composed
):
    # Reuse the existing real retained-byte configured fixture without changing
    # its contracts. Its older test modules use local imports, hence this scoped
    # test-only path; no production package is substituted.
    from pathlib import Path
    from uuid import UUID

    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "rdagent"))
    from test_rdagent_configured_backup import cold
    from test_rdagent_configured_inputs import case
    from test_rdagent_configured_source import retained
    from test_rdagent_configured_source_service import setup_source

    from backend.app import execution_snapshot_history as snapshots
    from backend.app import factor_analysis_history as analysis
    from backend.app.factor_native_io import native_storage_guard

    base = setup_source.__wrapped__(tmp_path, retained.__wrapped__(), monkeypatch)
    if composed:
        from packages.factors.contracts import canonical_bytes
        from packages.rdagent_adapter.contracts import FactorResearchConfig

        raw = base.config.model_dump(mode="json")
        raw.update(
            controller_profile="mineagent.rdagent-orchestrator.v3", max_iterations=1
        )
        base.config = FactorResearchConfig.model_validate_json(canonical_bytes(raw))
        base.plan.config_sha256 = base.config.sha256
        from backend.app import rdagent_configured_source as source_service

        monkeypatch.setattr(
            source_service,
            "require_research_spec",
            lambda *_: (NS(id=base.plan.spec_id), base.config),
        )
    configured = case.__wrapped__(base, monkeypatch)
    witnesses, arguments = cold.__wrapped__(configured)
    tables = dict(arguments["tables"], factor_configured_input_witnesses=witnesses)
    if drift:
        witnesses[0]["input_sha256"] = "f" * 64
    selected = []

    class DB:
        def scalar(self, statement):
            return NS(label_source_dataset_id=configured.dataset.id)

        def scalars(self, statement):
            if "alembic_version" in str(statement):
                return NS(all=lambda: [revision])
            model = statement.column_descriptions[0]["entity"]
            name = model.__tablename__
            selected.append(name)
            return NS(all=lambda: tables.get(name, []))

        def get(self, model, identifier):
            assert identifier == configured.dataset.id
            return arguments["datasets"][0]

    # DB projections already carry the canonical rows from the actual fixture.
    # Exposure ledger validation is a separate explicitly doubled prerequisite;
    # the configured byte/witness validator and stable filesystem reader are real.
    monkeypatch.setattr(analysis, "row_document", lambda row: row)
    monkeypatch.setattr(snapshots, "check_exposure_history", lambda *_: None)
    publication = NS(
        owner_id=UUID(witnesses[0]["owner_id"]), run_id=UUID(witnesses[0]["run_id"])
    )
    (configured.settings.storage_root / "factors").mkdir(exist_ok=True)
    with native_storage_guard(configured.settings.storage_root):
        if drift:
            with pytest.raises(ValueError):
                snapshots.check_configured_history(
                    DB(), publication, configured.settings
                )
        else:
            snapshots.check_configured_history(DB(), publication, configured.settings)
    assert "factor_configured_input_witnesses" in selected
    assert "factor_objects" in selected


@pytest.mark.parametrize(
    "revision",
    [
        "027_rd_controller_v3",
        "027a_contract_repairs",
        "027b_provider_material",
        *REVISIONS,
        "031_future",
    ],
)
def test_actual_historical_session_revision_query_preserves_published_027(
    revision, tmp_path, monkeypatch
):
    from contextlib import nullcontext

    from sqlalchemy.orm import sessionmaker

    from backend.app import execution_snapshot_history as snapshots

    engine = create_engine("sqlite://")
    factory = sessionmaker(engine)
    (tmp_path / "factors").mkdir()
    try:
        with factory.begin() as session:
            session.execute(text("CREATE TABLE alembic_version(version_num TEXT)"))
            session.execute(
                text("INSERT INTO alembic_version VALUES (:v)"), {"v": revision}
            )
        # PostgreSQL lifecycle advisory lock is a declared precondition double;
        # transaction, version query and filesystem native guard remain real.
        monkeypatch.setattr(snapshots, "shared_lifecycle", lambda *_: nullcontext())
        if revision == "031_future":
            with (
                pytest.raises(DomainError),
                snapshots.historical_session(
                    factory, uuid4(), NS(storage_root=tmp_path)
                ),
            ):
                pytest.fail("Unknown schema admitted")
        else:
            with snapshots.historical_session(
                factory, uuid4(), NS(storage_root=tmp_path)
            ) as session:
                assert (
                    session.scalar(text("SELECT version_num FROM alembic_version"))
                    == revision
                )
    finally:
        engine.dispose()


@pytest.mark.parametrize("revision", REVISIONS)
def test_successor_oe_closure_versions_cannot_be_crossed(revision):
    caps = capabilities_for(revision)
    wrong = operations.SCHEMA if revision == REVISIONS[3] else operations.SCHEMA_V2
    with pytest.raises(ValueError, match="closure schema differs"):
        operations.closure_for(
            {"oe_structured_closure": {"schema_version": wrong, "tables": {}}}, caps
        )
