"""Exact recovery ordering and binding negatives; no Docker or PG claim."""

from contextlib import contextmanager
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from packages.research_runtime.process_contracts import ProcessError
from workers.research import oe_structured_engine_recovery as recovery


def installed(monkeypatch):
    events = []
    admission = NS(id=uuid4(), image_id="sha256:" + "a" * 64)
    parent = "sha256:" + "b" * 64
    settings = NS(storage_root="/guarded")

    class Session:
        def __enter__(self):
            events.append("transaction")
            return self

        def __exit__(self, *args):
            events.append("end_transaction")

        def commit(self):
            events.append("commit")

    class Factory:
        def __init__(self):
            self.kw = {"bind": object()}

        def __call__(self):
            return Session()

    @contextmanager
    def lifecycle(*args):
        events.append("lifecycle")
        yield
        events.append("end_lifecycle")

    @contextmanager
    def native(*args):
        events.append("native_guard")
        yield
        events.append("end_native_guard")

    class Supervisor:
        def __init__(self, image, actual_parent):
            assert (image, actual_parent) == (admission.image_id, parent)

        def stop_and_verify(self, root, unit):
            assert root == settings.storage_root and unit is admission
            assert events[-1] == "end_transaction"
            events.append("actual_stop")

    def binding(session, aid):
        assert aid == admission.id
        events.append("binding")
        return admission, parent

    monkeypatch.setattr(recovery, "shared_lifecycle", lifecycle)
    monkeypatch.setattr(recovery, "native_storage_guard", native)
    monkeypatch.setattr(recovery, "StructuredSupervisor", Supervisor)
    monkeypatch.setattr(recovery, "_binding", binding)
    monkeypatch.setattr(
        recovery,
        "record_structured_containment",
        lambda *a, **k: events.append("record_stop"),
    )
    return Factory(), settings, admission, parent, events


def test_actual_stop_is_between_transactions_then_exact_recheck_before_commit(
    monkeypatch,
):
    factory, settings, admission, _, events = installed(monkeypatch)
    assert recovery.recover_engine_unit(factory, settings, admission.id) is True
    assert events == [
        "lifecycle",
        "native_guard",
        "transaction",
        "binding",
        "end_transaction",
        "actual_stop",
        "transaction",
        "binding",
        "record_stop",
        "commit",
        "end_transaction",
        "end_native_guard",
        "end_lifecycle",
    ]


def test_unobserved_identity_does_not_write_stop_or_settle(monkeypatch):
    factory, settings, admission, _, events = installed(monkeypatch)

    class Supervisor:
        def __init__(self, *args):
            pass

        def stop_and_verify(self, *args):
            raise ProcessError("structured_process_identity_unobserved")

    monkeypatch.setattr(recovery, "StructuredSupervisor", Supervisor)
    with pytest.raises(ProcessError):
        recovery.recover_engine_unit(factory, settings, admission.id)
    assert "record_stop" not in events and "commit" not in events


def test_poststop_binding_drift_remains_unknown_no_record_or_commit(monkeypatch):
    factory, settings, admission, parent, events = installed(monkeypatch)
    bindings = iter([(admission, parent), (admission, "sha256:" + "c" * 64)])
    monkeypatch.setattr(recovery, "_binding", lambda *args: next(bindings))
    with pytest.raises(ProcessError, match="oe_structured_recovery_binding"):
        recovery.recover_engine_unit(factory, settings, admission.id)
    assert (
        "actual_stop" in events
        and "record_stop" not in events
        and "commit" not in events
    )


def test_schema_mismatch_fails_before_any_identity_read():
    class Session:
        def scalars(self, query):
            return NS(all=lambda: ["026_rd_seeded_submissions"])

        def get(self, *args):
            raise AssertionError("Must not read an old-profile binding")

    with pytest.raises(ProcessError, match="oe_structured_recovery_schema"):
        recovery._binding(Session(), uuid4())


def binding_fixture(monkeypatch):
    from tests.structured_factor_strategy.test_process_journal import records

    launch, *_ = records()
    admission, inputs = launch.intent.admission, launch.intent.inputs
    scope = {
        key: getattr(admission, key)
        for key in ("owner_id", "run_id", "attempt_id", "generation")
    }
    candidate = NS(
        id=uuid4(), program_version_id=inputs.structured_strategy_version_id, **scope
    )
    execution = NS(
        id=inputs.execution_id,
        candidate_id=candidate.id,
        program_version_id=inputs.structured_strategy_version_id,
        snapshot_id=inputs.snapshot_id,
        strategy_input_sha256=inputs.strategy_input_sha256,
        strategy_input_size_bytes=inputs.strategy_input_size_bytes,
        market_sha256=inputs.market_sha256,
        market_size_bytes=inputs.market_size_bytes,
        **scope,
    )
    config = NS(
        fixed_inputs=NS(
            snapshot_id=inputs.snapshot_id,
            market_sha256=inputs.market_sha256,
            market_size_bytes=inputs.market_size_bytes,
        ),
        runtime=NS(
            engine_image_id=admission.image_id,
            engine_installed_parent_image_id="sha256:" + "e" * 64,
        ),
    )

    class Session:
        def scalars(self, query):
            return NS(all=lambda: ["028_oe_structured_search"])

        def scalar(self, query):
            # Queries/locks doubled, not a PostgreSQL assertion.
            return NS()

        def get(self, model, identifier, **kwargs):
            return {
                recovery.ResearchProcessAdmission: admission,
                recovery.ResearchProcessState: NS(),
                recovery.OEStructuredExecution: execution,
                recovery.OEStructuredCandidate: candidate,
            }.get(model)

    monkeypatch.setattr(recovery, "_try_neutral_lock", lambda *args: None)
    monkeypatch.setattr(
        recovery,
        "_persisted",
        lambda *args: (
            admission,
            None,
            {"launch_intent": NS(payload=launch.intent.model_dump(mode="json"))},
        ),
    )
    monkeypatch.setattr(recovery, "decode_spec", lambda *args: (None, config))
    monkeypatch.setattr(recovery, "decode_candidate", lambda *args: None)
    return Session(), admission, execution, candidate, config


def test_real_typed_intent_matches_execution_not_an_execution_permission(monkeypatch):
    session, admission, _, _, config = binding_fixture(monkeypatch)
    assert recovery._binding(session, admission.id) == (
        admission,
        config.runtime.engine_installed_parent_image_id,
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("owner_id", uuid4()),
        ("snapshot_id", uuid4()),
        ("program_version_id", uuid4()),
        ("strategy_input_sha256", "f" * 64),
        ("market_size_bytes", 123),
    ],
)
def test_recovery_cannot_rebind_same_profile_unit(monkeypatch, field, value):
    session, admission, execution, _, _ = binding_fixture(monkeypatch)
    setattr(execution, field, value)
    with pytest.raises(ProcessError, match="oe_structured_recovery_binding"):
        recovery._binding(session, admission.id)
