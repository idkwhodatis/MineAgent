"""Real retained-file replay with explicit DB/authority reader-boundary doubles.

This proves the optimized routing never caches across calls; it is not a full
PostgreSQL/native-process result acceptance test.
"""

import pytest

from backend.app.execution_snapshot_io import (
    publish_snapshot_bytes,
    verify_snapshot_bytes,
)
from backend.app.factor_native_io import native_storage_guard
from tests.factors.test_execution_snapshot_io import physical_fixture
from tests.rdagent import test_rdagent_configured_evaluation as configured
from tests.rdagent import test_rdagent_operational_evaluation as maintained
from tests.rdagent.test_rdagent_configured_evaluation import case  # noqa: F401
from tests.rdagent.test_rdagent_operational_evaluation import scenario  # noqa: F401


@pytest.mark.parametrize("kind", ["maintained", "configured"])
@pytest.mark.parametrize("change", ["tamper", "missing"])
def test_completed_reuse_freshly_checks_retained_files(
    kind, change, request, monkeypatch, tmp_path
):
    module = maintained if kind == "maintained" else configured
    fixture = request.getfixturevalue("scenario" if kind == "maintained" else "case")
    reader_name = (
        "read_evaluation_snapshot"
        if kind == "maintained"
        else "read_configured_evaluation"
    )
    original = getattr(module.service, reader_name)
    journal, physical = physical_fixture()
    (tmp_path / "factors").mkdir()
    reads = []

    def read(*args):
        reads.append(True)
        verify_snapshot_bytes(tmp_path, journal)
        return original(*args)

    monkeypatch.setattr(module.service, reader_name, read)
    with native_storage_guard(tmp_path):
        publish_snapshot_bytes(tmp_path, journal, physical)
        first = module.execute(fixture)
        assert module.execute(fixture) == first
        target = tmp_path / journal.manifest.assembled_rows.storage_key
        if change == "missing":
            target.unlink()
        else:
            target.chmod(0o600)
            target.write_bytes(b"changed retained bytes")
        with pytest.raises((ValueError, OSError)):
            module.execute(fixture)
    assert len(reads) == 3


@pytest.mark.parametrize("kind", ["maintained", "configured"])
def test_completed_reuse_rechecks_peer_at_reader_entry(kind, request, monkeypatch):
    module = maintained if kind == "maintained" else configured
    fixture = request.getfixturevalue("scenario" if kind == "maintained" else "case")
    name = (
        "read_evaluation_snapshot"
        if kind == "maintained"
        else "read_configured_evaluation"
    )
    original = getattr(module.service, name)

    def revoked_after_initial_scope(*args):
        fixture.state.peer_lost = True
        return original(*args)

    monkeypatch.setattr(module.service, name, revoked_after_initial_scope)
    with pytest.raises(module.RPCError, match="synthetic_peer_lost"):
        module.execute(fixture)
    assert fixture.state.events.count("authorize") == 2


@pytest.mark.parametrize("kind", ["maintained", "configured"])
def test_completed_reuse_does_not_return_before_transaction_exit(
    kind, request, monkeypatch
):
    from contextlib import contextmanager

    module = maintained if kind == "maintained" else configured
    fixture = request.getfixturevalue("scenario" if kind == "maintained" else "case")
    original = module.service.snapshot_transaction

    @contextmanager
    def lost_on_exit(*args):
        with original(*args) as session:
            yield session
            raise module.RPCError("transaction_exit_fence_lost")

    monkeypatch.setattr(module.service, "snapshot_transaction", lost_on_exit)
    with pytest.raises(module.RPCError, match="transaction_exit_fence_lost"):
        module.execute(fixture)
