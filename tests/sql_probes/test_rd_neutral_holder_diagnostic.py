import json

from script import workflow_heartbeat_production_report as report
from tests.integration.rd_neutral_holder_diagnostic import _classify, _key_parts


def test_classification_requires_unique_holder_stable_and_exact_identity():
    lease = ("owner", "run", "attempt", 1, "token")
    empty = (0, {})
    active = (1, {43: lease})
    assert _classify([43], active, active, lease, 42) == "workflow_heartbeat"
    assert _classify([43], active, active, lease[:-1], 42) == "other"
    assert _classify([43], empty, active, lease, 42) == "unstable"
    assert _classify([], active, active, lease, 42) == "unstable"
    assert _classify([43, 44], active, active, lease, 42) == "unstable"
    assert _classify([42], active, active, lease, 42) == "unobserved"
    assert _classify([43], empty, empty, lease, 42) == "other"


def test_split_matches_postgres_bigint_layout():
    import hashlib

    key = int.from_bytes(hashlib.sha256(b"test").digest()[:8], "big", signed=True)
    parts = _key_parts("test")
    assert parts["hi"] * 2**32 + parts["lo"] == key % 2**64


def test_projection_never_exposes_ids_arbitrary_values_or_duplicate_evidence():
    prefix = "RD_NEUTRAL_HOLDER_V1:"
    fields = {
        "lock_scope": "owner",
        "holder": "workflow_heartbeat",
        "caller": "rpc_auth",
    }
    line = prefix + json.dumps(fields)
    assert report._drain_observations(line) == {
        "first_lock_scope": "owner",
        "first_holder": "workflow_heartbeat",
        "first_caller": "rpc_auth",
    }
    assert report._drain_observations(line + "\n" + line) == {}
    assert report._drain_observations(prefix + json.dumps(fields | {"pid": 4321})) == {}
    assert (
        report._drain_observations(prefix + json.dumps(fields | {"holder": "secret"}))
        == {}
    )


def test_hooks_sample_only_first_busy_and_preserve_original_error(monkeypatch, capsys):
    from contextlib import contextmanager
    from types import SimpleNamespace as NS
    from uuid import uuid4

    import pytest

    from backend.app import research_lifecycle as lifecycle
    from backend.app.services import DomainError
    from packages.research_runtime.fences import AttemptFence
    from tests.integration import rd_neutral_holder_diagnostic as diagnostic
    from workers.research import rdagent_workflow_lease as leases

    for key, value in diagnostic._EXPECTED.items():
        monkeypatch.setenv(key, value)
    run = uuid4()
    fence = AttemptFence(uuid4(), run, uuid4(), 1, uuid4())
    scope = f"submit:{fence.owner_id}"
    engine = NS(
        dialect=NS(name="postgresql"),
        url=NS(database=f"tradegrad_backup_test_{uuid4().hex}"),
    )
    calls = []

    def conn(pid):
        return NS(
            engine=engine, connection=NS(driver_connection=NS(info=NS(backend_pid=pid)))
        )

    pulse_session = NS(connection=lambda: conn(43))

    def sample(query, params):
        assert query is diagnostic._HOLDERS and params == diagnostic._key_parts(scope)
        calls.append("sample")
        return NS(scalars=lambda: NS(all=lambda: [43]))

    neutral_session = NS(connection=lambda: conn(42), execute=sample)
    failure = DomainError("neutral_busy", "private canary", 409)

    @contextmanager
    def transaction(_factory, _fence, *, resource_scope="none"):
        assert resource_scope == "none"
        lifecycle._try_neutral_lock(neutral_session, scope)
        pytest.fail("original rejection must escape")
        yield neutral_session

    def try_lock(session, key):
        assert session is neutral_session and key == scope
        calls.append("original_try")
        raise failure

    @contextmanager
    def pulse(_self):
        leases.advisory_lock(pulse_session, scope)
        yield pulse_session

    def lock(session, key):
        assert session is pulse_session and key == scope
        calls.append("original_pulse_lock")

    monkeypatch.setattr(lifecycle, "_neutral_transaction_once", transaction)
    monkeypatch.setattr(lifecycle, "_try_neutral_lock", try_lock)
    monkeypatch.setattr(leases.WorkflowAttemptLease, "_pulse_transaction", pulse)
    monkeypatch.setattr(leases, "advisory_lock", lock)
    lease = NS(
        _params={key: getattr(fence, key) for key in diagnostic._FIELDS},
        _owner_scope=scope,
    )
    with (
        diagnostic.observe_first_neutral_holder(engine, run),
        leases.WorkflowAttemptLease._pulse_transaction(lease),
    ):
        for _ in range(2):
            with (
                pytest.raises(DomainError) as caught,
                lifecycle._neutral_transaction_once(None, fence),
            ):
                pytest.fail("original exception must escape")
            assert caught.value is failure

    assert calls == ["original_pulse_lock", "original_try", "sample", "original_try"]
    assert lifecycle._neutral_transaction_once is transaction
    assert lifecycle._try_neutral_lock is try_lock
    assert leases.WorkflowAttemptLease._pulse_transaction is pulse
    assert leases.advisory_lock is lock
    assert report._drain_observations(capsys.readouterr().out) == {
        "first_lock_scope": "owner",
        "first_holder": "workflow_heartbeat",
        "first_caller": "other",
    }
