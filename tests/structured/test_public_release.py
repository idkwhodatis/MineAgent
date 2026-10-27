"""Inert transaction ordering only; genuine025 HTTP/browser/cold gates are pending."""

from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import structured_result_release as release
from backend.app import structured_result_routes as routes
from backend.app.services import DomainError


@pytest.fixture
def harness(monkeypatch):
    owner, run_id = uuid4(), uuid4()
    execution_ids = (uuid4(), uuid4())
    events, recorded = [], []
    state = NS(missing=None, fail_binding=None, fail_commit=False, tamper=False)
    buffers = {key: object() for key in execution_ids}

    @contextmanager
    def session():
        yield object()

    @contextmanager
    def transaction():
        events.append("begin")
        try:
            yield object()
            if state.fail_commit:
                raise RuntimeError("commit failed")
            events.append("commit")
        except Exception:
            events.append("rollback")
            raise

    class Factory:
        def __init__(self):
            self.kw = {"bind": object()}

        __call__ = staticmethod(session)
        begin = staticmethod(transaction)

    @contextmanager
    def lifecycle(*args):
        events.append("guard")
        yield

    def receipts(session, owner_id, execution_id):
        if execution_id == state.missing:
            raise DomainError("engine_result_release_required", "Missing", 409)

    def reader(factory, owner_id, execution_id, settings):
        events.append(("read", execution_id))
        return buffers[execution_id]

    dataset = NS(
        id=uuid4(),
        start_date=date(2024, 1, 1),
        end_date=date(2024, 3, 1),
        content_sha256="a" * 64,
    )

    def bindings(session, owner_id, run, execution_id, buffer):
        events.append(("bindings", execution_id))
        assert buffer is buffers[execution_id]
        if execution_id == state.fail_binding:
            release.inconsistent()
        return NS(id=run_id, experiment_id=None, split_version_id=None), [
            (role, dataset, {"execution_id": str(execution_id)})
            for role in ("factor", "market")
        ]

    def record(*args, **kwargs):
        recorded.append(kwargs)
        return NS(
            id=uuid4(),
            owner_id=owner,
            run_id=uuid4() if state.tamper else run_id,
            experiment_id=None,
            split_version_id=None,
        )

    monkeypatch.setattr(release, "owned_execution", lambda *a: None)
    monkeypatch.setattr(release, "require_receipts", receipts)
    monkeypatch.setattr(release, "read_historical_structured_execution", reader)
    monkeypatch.setattr(release, "shared_lifecycle", lifecycle)
    monkeypatch.setattr(
        release, "advisory_lock", lambda *a: events.append("owner_lock")
    )
    monkeypatch.setattr(release, "bindings", bindings)
    monkeypatch.setattr(release, "record_exposure", record)
    return NS(
        factory=Factory(),
        settings=NS(storage_root="unused"),
        args={"owner_id": owner, "run_id": run_id, "execution_ids": execution_ids},
        events=events,
        recorded=recorded,
        state=state,
        buffers=buffers,
        dataset=dataset,
    )


def test_pair_requires_both_existing_receipts_before_either_private_read(harness):
    h = harness
    h.state.missing = h.args["execution_ids"][1]
    with pytest.raises(DomainError, match="Missing"):
        release.disclose_structured(
            h.factory, h.settings, **h.args, create_release=False
        )
    assert not h.events and not h.recorded


def test_private_buffers_precede_single_owner_first_commit_and_full_extent(harness):
    h = harness
    buffers, ids = release.disclose_structured(
        h.factory, h.settings, **h.args, create_release=False
    )
    assert buffers == tuple(h.buffers.values()) and len(ids) == 4
    assert h.events[:5] == [("read", key) for key in h.args["execution_ids"]] + [
        "guard",
        "begin",
        "owner_lock",
    ]
    assert h.events[-1] == "commit"
    for record in h.recorded:
        assert record["window"].start == h.dataset.start_date
        assert record["window"].end == h.dataset.end_date
        assert record["provenance"]["dataset_version_id"] == str(h.dataset.id)
    assert len({r["source_key"] for r in h.recorded}) == 4


@pytest.mark.parametrize("failure", ["binding", "commit", "actual_ledger_fk"])
def test_no_values_escape_failed_second_phase(harness, failure):
    h = harness
    if failure == "binding":
        h.state.fail_binding = h.args["execution_ids"][1]
    if failure == "commit":
        h.state.fail_commit = True
    if failure == "actual_ledger_fk":
        h.state.tamper = True
    with pytest.raises((DomainError, RuntimeError)):
        release.disclose_structured(
            h.factory, h.settings, **h.args, create_release=True
        )
    assert h.events[-1] == "rollback" and "commit" not in h.events


def test_duplicate_pair_rejected_before_read_or_exposure(harness):
    h = harness
    h.args["execution_ids"] = (h.args["execution_ids"][0],) * 2
    with pytest.raises(DomainError) as error:
        release.disclose_structured(
            h.factory, h.settings, **h.args, create_release=False
        )
    assert error.value.code == "invalid_structured_pair" and not h.events


def test_comparison_projection_never_runs_before_access_succeeds(monkeypatch):
    def denied(*a, **k):
        raise DomainError("engine_result_release_required", "Missing", 409)

    monkeypatch.setattr(routes, "access", denied)
    monkeypatch.setattr(
        routes,
        "compare_released_pair",
        lambda *a: pytest.fail("No flags before both release receipts"),
    )
    with pytest.raises(DomainError):
        routes.read_comparison(uuid4(), uuid4(), uuid4(), NS(id=uuid4()), object())


@pytest.fixture
def binding_graph(monkeypatch):
    from packages.factors.contracts import digest
    from tests.structured.test_views import histories

    h = histories(changed=True)[0]
    h.admission_id = uuid4()
    owner = uuid4()
    run = NS(id=h.run_id, owner_id=owner)
    config = NS(dataset_version_id=uuid4(), dataset_sha256="d" * 64)
    program = {"closed": True}
    execution = NS(
        id=h.execution_id,
        structured_strategy_version_id=h.structured_strategy_version_id,
        factor_version_id=h.factor_version_id,
        snapshot_id=h.snapshot_id,
        strategy_input_sha256=h.result.strategy_input_sha256,
        market_sha256=h.result.market_sha256,
        attempt_id=uuid4(),
        generation=1,
    )
    outcome = NS(
        owner_id=owner,
        run_id=run.id,
        attempt_id=execution.attempt_id,
        generation=1,
        admission_id=h.admission_id,
        result_sha256=h.result_sha256,
    )
    publication = NS(
        id=h.snapshot_id,
        run_id=h.execution_snapshot_run_id,
        owner_id=owner,
        factor_version_id=h.factor_version_id,
    )
    version = NS(
        owner_id=owner,
        factor_id=h.factor_id,
        parent_version_id=None,
        code_sha256=h.factor_code_sha256,
        definition_sha256=digest(h.factor_definition),
    )
    dataset = NS(
        id=config.dataset_version_id,
        owner_id=owner,
        content_sha256=config.dataset_sha256,
    )
    values = {
        release.StructuredExecutionOutcome: outcome,
        release.ExecutionSnapshotPublication: publication,
        release.FactorVersion: version,
        release.DatasetVersion: dataset,
    }
    session = NS(
        get=lambda model, key: values[model],
        scalar=lambda query: NS(id=publication.run_id),
        execute=lambda *a: None,
    )
    monkeypatch.setattr(release, "owned_execution", lambda *a: (run, execution))
    monkeypatch.setattr(
        release, "decode_structured_spec", lambda *a: (run, None, config, program)
    )
    monkeypatch.setattr(
        release,
        "snapshot_bindings",
        lambda *a: (dataset, {"input_sha256": h.snapshot_input_sha256}),
    )
    original_digest = release.digest
    monkeypatch.setattr(
        release,
        "digest",
        lambda value: "config-hash" if value is config else original_digest(value),
    )
    return NS(
        h=h,
        owner=owner,
        run=run,
        execution=execution,
        outcome=outcome,
        publication=publication,
        version=version,
        dataset=dataset,
        session=session,
    )


def test_exact_immutable_binding_produces_distinct_full_source_and_market_roles(
    binding_graph,
):
    g = binding_graph
    run, records = release.bindings(g.session, g.owner, g.run.id, g.execution.id, g.h)
    assert run is g.run and [x[0] for x in records] == ["factor", "market"]
    assert records[0][2]["result_sha256"] == g.h.result_sha256


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("outcome", "result_sha256", "f" * 64),
        ("execution", "strategy_input_sha256", "f" * 64),
        ("execution", "market_sha256", "f" * 64),
        ("outcome", "owner_id", None),
        ("publication", "factor_version_id", None),
        ("publication", "run_id", None),
        ("version", "parent_version_id", None),
        ("dataset", "content_sha256", "f" * 64),
        ("dataset", "owner_id", None),
    ],
)
def test_phase_two_rejects_changed_hash_dataset_owner_or_parent(
    binding_graph, target, field, value
):
    g = binding_graph
    if target == "version":
        g.h.parent_factor_version_id = uuid4()
    setattr(getattr(g, target), field, value)
    with pytest.raises(DomainError) as error:
        release.bindings(g.session, g.owner, g.run.id, g.execution.id, g.h)
    assert error.value.code == "structured_result_inconsistent"


def test_owner_absence_precedes_schema_or_result_projection(monkeypatch):
    monkeypatch.setattr(
        release,
        "require_structured_capability",
        lambda *a: pytest.fail("No foreign schema/result work"),
    )
    with pytest.raises(DomainError) as error:
        release.owned_execution(NS(scalar=lambda *a: None), uuid4(), uuid4(), uuid4())
    assert error.value.status_code == 404


def test_phase_two_checks_canonical_buffer_not_only_attached_hash(binding_graph):
    g = binding_graph
    g.h.result = g.h.result.model_copy(update={"market_sha256": "f" * 64})
    g.execution.market_sha256 = g.h.result.market_sha256
    with pytest.raises(DomainError) as error:
        release.bindings(g.session, g.owner, g.run.id, g.execution.id, g.h)
    assert error.value.code == "structured_result_inconsistent"
