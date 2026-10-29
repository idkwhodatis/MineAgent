"""Normal027 disclosure transaction ordering with explicit reader/DB doubles."""

from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import oe_structured_result_history as history
from backend.app import oe_structured_result_release as release
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

    def reader(factory, settings, *, owner_id, execution_id):
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
            release.fail()
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
    monkeypatch.setattr(history, "read_result", reader)
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
        release.disclose(h.factory, h.settings, **h.args, create_release=False)
    assert not h.events and not h.recorded


def test_private_buffers_precede_single_owner_first_commit_and_full_extent(harness):
    h = harness
    buffers, ids = release.disclose(
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
        release.disclose(h.factory, h.settings, **h.args, create_release=True)
    assert h.events[-1] == "rollback" and "commit" not in h.events


def test_duplicate_pair_rejected_before_read_or_exposure(harness):
    h = harness
    h.args["execution_ids"] = (h.args["execution_ids"][0],) * 2
    with pytest.raises(DomainError) as error:
        release.disclose(h.factory, h.settings, **h.args, create_release=False)
    assert error.value.code == "invalid_structured_pair" and not h.events


@pytest.fixture
def binding_graph(monkeypatch):
    from packages.factors.contracts import canonical_bytes, digest
    from tests.openevolve.test_structured_result_views import pair

    verified = pair()[0]
    value = verified.execution
    value.admission_id = uuid4()
    owner = uuid4()
    run = NS(id=value.run_id, owner_id=owner)
    objective = {"fixed_objective": "binding-test"}
    verified.measurement = verified.measurement.model_copy(
        update={"objective_sha256": digest(objective)}
    )
    verified.measurement_sha256 = digest(verified.measurement)
    config = {
        "fixed_inputs": {
            "factor_version_id": value.factor_version_id,
            "snapshot_id": value.snapshot_id,
            "snapshot_input_sha256": value.snapshot_input_sha256,
            "dataset_version_id": uuid4(),
            "dataset_sha256": "d" * 64,
        },
        "search": {"objective": objective},
    }
    # JSON-compatible projection, no authority parser replaced by this fixture.
    config = NS(
        fixed_inputs=NS(**config["fixed_inputs"]), search=NS(objective=objective)
    )
    original_digest = release.digest
    monkeypatch.setattr(
        release,
        "digest",
        lambda v: verified.config_sha256 if v is config else original_digest(v),
    )
    candidate = NS(
        id=verified.candidate_id,
        program_version_id=value.structured_strategy_version_id,
        program_sha256="c" * 64,
    )
    execution = NS(
        id=value.execution_id,
        owner_id=owner,
        run_id=run.id,
        candidate_id=candidate.id,
        program_version_id=value.structured_strategy_version_id,
        snapshot_id=value.snapshot_id,
        attempt_id=uuid4(),
        generation=1,
        strategy_input_sha256=value.result.strategy_input_sha256,
        market_sha256=value.result.market_sha256,
    )
    outcome = NS(
        owner_id=owner,
        run_id=run.id,
        attempt_id=execution.attempt_id,
        generation=1,
        candidate_id=candidate.id,
        admission_id=value.admission_id,
        result_sha256=value.result_sha256,
        result_canonical=canonical_bytes(value.result).decode("ascii"),
        measurement_canonical=canonical_bytes(verified.measurement).decode("ascii"),
        measurement_sha256=verified.measurement_sha256,
        objective_sha256=digest(objective),
    )
    publication = NS(
        owner_id=owner,
        run_id=value.execution_snapshot_run_id,
        factor_version_id=value.factor_version_id,
    )
    version = NS(
        owner_id=owner,
        factor_id=value.factor_id,
        parent_version_id=value.parent_factor_version_id,
        code_sha256=value.factor_code_sha256,
        definition_sha256=digest(value.factor_definition),
    )
    dataset = NS(owner_id=owner, content_sha256=config.fixed_inputs.dataset_sha256)
    rows = {
        release.OEStructuredCandidate: candidate,
        release.ExecutionSnapshotPublication: publication,
        release.FactorVersion: version,
        release.DatasetVersion: dataset,
    }
    session = NS(
        get=lambda model, key: rows[model],
        execute=lambda *a: None,
        scalar=lambda q: (
            outcome
            if q.column_descriptions[0]["entity"] is release.OEStructuredEngineOutcome
            else NS(id=publication.run_id)
        ),
    )
    monkeypatch.setattr(release, "owned_execution", lambda *a: (run, execution, config))
    monkeypatch.setattr(release, "decode_candidate", lambda *a: None)
    monkeypatch.setattr(
        release,
        "snapshot_bindings",
        lambda *a: (dataset, {"input_sha256": value.snapshot_input_sha256}),
    )
    return NS(
        verified=verified,
        run=run,
        owner=owner,
        execution=execution,
        outcome=outcome,
        candidate=candidate,
        publication=publication,
        version=version,
        dataset=dataset,
        session=session,
    )


def test_exact_normal_result_and_measurement_bind_both_disclosure_roles(binding_graph):
    g = binding_graph
    _, rows = release.bindings(g.session, g.owner, g.run.id, g.execution.id, g.verified)
    assert [row[0] for row in rows] == ["factor", "market"]
    assert rows[0][2]["measurement_sha256"] == g.verified.measurement_sha256
    assert rows[0][2]["candidate_id"] == str(g.candidate.id)


@pytest.mark.parametrize(
    "target,field",
    [
        ("outcome", "candidate_id"),
        ("candidate", "program_version_id"),
        ("outcome", "result_sha256"),
        ("outcome", "measurement_sha256"),
        ("outcome", "measurement_canonical"),
        ("outcome", "objective_sha256"),
        ("execution", "snapshot_id"),
        ("execution", "market_sha256"),
        ("publication", "run_id"),
        ("version", "code_sha256"),
        ("dataset", "owner_id"),
        ("dataset", "content_sha256"),
    ],
)
def test_disclosure_rejects_same_scope_or_buffer_binding_drift(
    binding_graph, target, field
):
    g = binding_graph
    setattr(getattr(g, target), field, None)
    with pytest.raises(DomainError):
        release.bindings(g.session, g.owner, g.run.id, g.execution.id, g.verified)
