"""Two-phase boundary tests; no hosted release/PG acceptance claim."""

from contextlib import contextmanager
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import research_workflow_feedback as service
from backend.app.factor_analysis_history import HistoricalDirectAnalysis
from backend.app.services import DomainError
from packages.factors.contracts import digest
from packages.qlib_adapter.execution_binding import bind_completed_snapshot
from packages.qlib_adapter.objective import measure_direct_objective
from tests.qlib.test_execution_analysis_binding import inputs, measured, objective


@pytest.fixture
def graph(monkeypatch):
    projection = bind_completed_snapshot(**inputs())
    result = measured(projection)
    measurement = measure_direct_objective(objective(projection), projection, result)
    checkpoint = NS(
        child_run_id=uuid4(),
        analysis_id=uuid4(),
        analysis_result_sha256=digest(result),
        analysis_measurement_sha256=digest(measurement),
    )
    verified = HistoricalDirectAnalysis(
        checkpoint.analysis_id,
        uuid4(),
        uuid4(),
        digest(result),
        digest(measurement),
        result,
        measurement,
    )
    ids = (uuid4(), uuid4())
    row = NS(id=uuid4())
    plan = NS(allow_workflow_development_feedback_release=False)
    trace = []

    @contextmanager
    def historical(*a):
        trace.append("locked")
        try:
            yield object()
        finally:
            trace.append("unlocked")

    monkeypatch.setattr(service, "historical_session", historical)
    monkeypatch.setattr(
        service, "_binding", lambda *a: (None, None, plan, None, row, checkpoint)
    )
    monkeypatch.setattr(service, "verify_rd_checkpoint", lambda *a: (row, checkpoint))

    def disclose(*a, **kwargs):
        assert trace[-1] == "unlocked"
        trace.append(("disclose", kwargs["create_release"]))
        return verified, ids

    monkeypatch.setattr(service, "disclose", disclose)
    return NS(
        checkpoint=checkpoint,
        verified=verified,
        plan=plan,
        trace=trace,
        owner=uuid4(),
        workflow=uuid4(),
        stage=uuid4(),
        ids=ids,
    )


@pytest.mark.parametrize("optin", [False, True])
def test_disclose_outside_owner_lock_only_initial_policy(graph, optin):
    g = graph
    g.plan.allow_workflow_development_feedback_release = optin
    value = service.prepare_feedback_read(
        None, None, owner_id=g.owner, workflow_id=g.workflow, stage_id=g.stage
    )
    assert value.exposure_ids == g.ids and g.trace == [
        "locked",
        "unlocked",
        ("disclose", optin),
    ]


def test_missing_release_stays_waiting_not_terminal(graph, monkeypatch):
    def missing(*a, **k):
        raise DomainError("engine_result_release_required", "required", 409)

    monkeypatch.setattr(service, "disclose", missing)
    with pytest.raises(DomainError, match="development feedback") as exc:
        service.prepare_feedback_read(
            None,
            None,
            owner_id=graph.owner,
            workflow_id=graph.workflow,
            stage_id=graph.stage,
        )
    assert exc.value.code == "workflow_feedback_release_required"


def test_private_buffer_cannot_change_measurement_under_old_hash(graph):
    from dataclasses import replace

    altered = replace(
        graph.verified,
        measurement=graph.verified.measurement.model_copy(update={"value": 0.9}),
    )
    with pytest.raises(DomainError):
        service._verify_buffer(altered, graph.checkpoint)


def test_http_shaped_fake_buffer_rejected_before_binding(monkeypatch):
    monkeypatch.setattr(service, "require_publication_admission", lambda *a: None)
    with pytest.raises(DomainError):
        service.revalidate_feedback_read(
            None, uuid4(), uuid4(), uuid4(), None, {}, admission=object()
        )


from tests.research_workflow.test_rd_checkpoint import boundary  # noqa: F401
from tests.research_workflow.test_rd_terminal import completed_graph  # noqa: F401


@pytest.mark.parametrize(
    "change", [None, "wrong_child", "wrong_output", "nonadjacent", "foreign_checkpoint"]
)
def test_actual_feedback_definition_joins_prior_owned_checkpoint(boundary, change):  # noqa: F811
    from packages.factors.contracts import canonical_bytes
    from packages.research_inputs.contracts import SourceOutputRef
    from packages.research_inputs.feedback import FeedbackProjectionDefinition

    b = boundary
    facts = b.actual.model_copy(
        update={
            "owner_id": b.root.owner_id,
            "definition_sha256": b.stage.definition_sha256,
        }
    )
    b.stage.child_run_id = facts.child_run_id
    definition = FeedbackProjectionDefinition(
        source=SourceOutputRef(
            workflow_id=b.root.id,
            stage_id=b.stage.id,
            child_run_id=facts.child_run_id,
            output_kind="direct_analysis",
            output_id=facts.analysis_id,
            content_sha256=facts.analysis_result_sha256,
        )
    )
    stage = NS(
        id=uuid4(),
        owner_id=b.root.owner_id,
        workflow_id=b.root.id,
        kind="experiment_feedback_projection",
        child_run_id=None,
        ordinal=2,
        definition_canonical=canonical_bytes(definition).decode(),
        definition_sha256=digest(definition),
    )
    row = NS(
        id=uuid4(),
        owner_id=b.root.owner_id,
        workflow_id=b.root.id,
        stage_id=b.stage.id,
        terminal_canonical=canonical_bytes(facts).decode(),
        terminal_sha256=digest(facts),
    )
    if change == "wrong_child":
        b.stage.child_run_id = uuid4()
    elif change == "nonadjacent":
        b.stage.ordinal = 3
    elif change == "foreign_checkpoint":
        row.owner_id = uuid4()
    elif change == "wrong_output":
        changed = facts.model_copy(update={"analysis_id": uuid4()})
        row.terminal_canonical = canonical_bytes(changed).decode()
        row.terminal_sha256 = digest(changed)
    rows = iter((b.root, stage, b.stage, row))
    session = NS(scalar=lambda q: next(rows))
    # Use real immutable binding decoder; only schema installation is a double.
    from unittest.mock import patch

    with patch.object(service, "require_schema", lambda s: None):
        if change:
            with pytest.raises(DomainError):
                service._binding(session, b.root.owner_id, b.root.id, stage.id)
        else:
            assert (
                service._binding(session, b.root.owner_id, b.root.id, stage.id)[-1]
                == facts
            )


@pytest.mark.parametrize(
    "change", [None, "missing_release", "wrong_release", "checkpoint_changed"]
)
def test_inside_publication_revalidates_exact_existing_release_only(
    graph, monkeypatch, change
):
    from datetime import date

    g = graph
    prepared = service.prepare_feedback_read(
        None, None, owner_id=g.owner, workflow_id=g.workflow, stage_id=g.stage
    )
    monkeypatch.setattr(service, "require_publication_admission", lambda *a: None)
    if change == "checkpoint_changed":
        monkeypatch.setattr(
            service,
            "_binding",
            lambda *a: (None, None, g.plan, None, NS(id=uuid4()), g.checkpoint),
        )
    run = NS(
        id=g.checkpoint.child_run_id,
        owner_id=g.owner,
        purpose="factor_research",
        engine="rdagent",
        experiment_id=None,
        split_version_id=None,
    )
    datasets = [
        NS(
            id=uuid4(),
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 2),
            content_sha256="b" * 64,
        )
        for _ in range(2)
    ]
    monkeypatch.setattr(
        service,
        "release_bindings",
        lambda *a: (
            run,
            list(zip(("factor", "label"), datasets, ({}, {}), strict=True)),
        ),
    )
    rows = [
        NS(
            id=identifier,
            owner_id=g.owner,
            run_id=run.id,
            experiment_id=None,
            split_version_id=None,
            dataset_version_id=dataset.id,
            source_key=service.source_key(
                "direct_analysis", g.checkpoint.analysis_id, role
            ),
            start_date=dataset.start_date,
            end_date=dataset.end_date,
            purpose="engine_direct_analysis_release",
            identities=[],
            provenance={
                "role": role,
                "dataset_version_id": str(dataset.id),
                "dataset_content_sha256": dataset.content_sha256,
            },
        )
        for identifier, dataset, role in zip(
            g.ids, datasets, ("factor", "label"), strict=True
        )
    ]
    if change == "wrong_release":
        rows[-1].id = uuid4()
    lookup = iter(rows if change != "missing_release" else [None])
    monkeypatch.setattr(service, "_identities", lambda *a: [])
    session = NS(scalar=lambda q: next(lookup))
    if change:
        with pytest.raises(DomainError):
            service.revalidate_feedback_read(
                session,
                g.owner,
                g.workflow,
                g.stage,
                None,
                prepared,
                admission=object(),
            )
    else:
        assert service.revalidate_feedback_read(
            session, g.owner, g.workflow, g.stage, None, prepared, admission=object()
        ) == (g.verified, g.ids)


def test_historical_read_never_creates_release_even_opted_in(graph):
    graph.plan.allow_workflow_development_feedback_release = True
    service.read_released_feedback(
        None,
        None,
        owner_id=graph.owner,
        workflow_id=graph.workflow,
        stage_id=graph.stage,
    )
    assert graph.trace[-1] == ("disclose", False)


def test_historical_revalidation_requires_real_guard_before_sql():
    from backend.app.factor_native_io import NativeStorageError

    with pytest.raises(NativeStorageError):
        service.revalidate_released_feedback(
            None, uuid4(), uuid4(), uuid4(), NS(storage_root="/no-guard"), object()
        )


@pytest.mark.parametrize("missing", [False, True])
def test_session_reader_checks_release_before_actual_bytes(graph, monkeypatch, missing):
    from backend.app import factor_analysis_history, factor_native_io

    g = graph
    monkeypatch.setattr(
        factor_native_io, "require_native_storage_guard", lambda *a: None
    )
    exposures = [
        NS(id=i, owner_id=g.owner, run_id=g.checkpoint.child_run_id) for i in g.ids
    ]
    rows = iter([None] if missing else exposures)
    calls = []
    monkeypatch.setattr(
        service,
        "verify_rd_checkpoint",
        lambda *a: calls.append("checkpoint") or (None, g.checkpoint),
    )
    monkeypatch.setattr(
        factor_analysis_history,
        "read_analysis_in_session",
        lambda *a: calls.append("bytes") or g.verified,
    )
    monkeypatch.setattr(
        service,
        "revalidate_released_feedback",
        lambda *a: calls.append("ledger_recheck"),
    )
    session = NS(scalar=lambda q: next(rows))
    if missing:
        with pytest.raises(DomainError):
            service.read_released_feedback_in_session(
                session, g.owner, g.workflow, g.stage, NS(storage_root="/unused")
            )
        assert not calls
    else:
        value = service.read_released_feedback_in_session(
            session, g.owner, g.workflow, g.stage, NS(storage_root="/unused")
        )
        assert value.exposure_ids == g.ids and calls == [
            "checkpoint",
            "bytes",
            "ledger_recheck",
        ]
