"""Real PostgreSQL contention at the current v3 heartbeat and RPC auth cut.

A maintained v3 attempt and the production transaction/commit/rollback paths
are real. Controller records and the kernel peer are explicit input doubles;
the separate, unchanged bridge owns actual controller/receipt acceptance.
"""

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event, current_thread
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import sessionmaker
from test_backup_restore import drill_databases as drill_databases  # noqa: PLC0414
from test_workflow_heartbeat_production import _lease_times
from workflow_structured_stage_fixture import actual_environment, create_workflow

pytestmark = pytest.mark.postgres


class _ExpectedRollback(Exception):
    """Abort only the tested controller-record lookup, after its DB locks."""


def _claimed_workflow(env):
    from backend.app.rdagent_workflow_models import FactorWorkflowSubmission
    from workers.research.rdagent_queue import claim_configured_run
    from workers.research.rdagent_workflow_lease import (
        WorkflowAttemptLease,
        configured_rd_lease,
    )
    from workers.research.workflow_tick import drive_one

    owner = env.account.user.id
    workflow = create_workflow(env)
    assert (
        drive_one(env.factory, env.settings, owner_id=owner, workflow_id=workflow).state
        == "child_queued"
    )
    with env.factory() as session:
        run = session.scalars(
            select(FactorWorkflowSubmission.run_id)
            .filter_by(owner_id=owner, workflow_id=workflow)
            .limit(2)
        ).one()
    worker = "owned-heartbeat-rpc-fixture"
    mode = []
    fence = claim_configured_run(
        env.factory, run, 1, worker, env.settings, _on_committed=mode.append
    )
    assert fence is not None and fence.owner_id == owner
    assert mode == [True]
    lease = configured_rd_lease(
        env.factory, env.settings, fence, worker, workflow=mode[0]
    )
    assert type(lease) is WorkflowAttemptLease
    return fence, lease


def _controller_callback(fence, effects, rollback_on, monkeypatch):
    from backend.app import rdagent_protected_controller as controller
    from backend.app.research_lifecycle import require_neutral_transaction
    from packages.rdagent_adapter.controller_profile import COMPOSED_CONTROLLER_PROFILE
    from packages.research_runtime.process_declarations_v2 import (
        HostTicketIPC,
        ProcessDeclarationV2,
        declaration_bytes,
    )
    from runtimes.rdagent_operational import peer
    from runtimes.rdagent_operational.daemon_identity import DaemonIdentityContext

    ticket = "a" * 64
    admission = ProcessDeclarationV2(
        schema_version="mineagent.process-admission.v2",
        id=uuid4(),
        owner_id=fence.owner_id,
        run_id=fence.run_id,
        attempt_id=fence.attempt_id,
        generation=fence.generation,
        role="rd_orchestrator",
        logical_operation_id=uuid4(),
        launch_ordinal=1,
        profile=COMPOSED_CONTROLLER_PROFILE,
        input_sha256="b" * 64,
        reservation_id=uuid4(),
        image_id="sha256:" + "c" * 64,
        ipc=HostTicketIPC(
            ipc_policy="host_ticket",
            ticket_sha256=hashlib.sha256(ticket.encode()).hexdigest(),
        ),
    )
    leader = peer.KernelIdentity(
        100, 1000, "pid:[12]", "0::/exact\n", 1, (0,) * 4, (0,) * 4
    )
    child = peer.KernelIdentity(
        101, 1001, "pid:[12]", "0::/exact\n", 100, (65533,) * 4, (65533,) * 4
    )
    binding = peer.TrustedControllerBinding(
        hashlib.sha256(declaration_bytes(admission)).hexdigest(), leader, child
    )
    deadline = monotonic() + 15
    context = DaemonIdentityContext(
        "d" * 64, admission.image_id, leader.host_pid, deadline, admission.profile
    )
    identity = SimpleNamespace(
        container_id=context.container_id, image_id=admission.image_id, leader=leader
    )

    def records(session, current):
        assert current is fence
        require_neutral_transaction(session, fence, resource_scope="processes")
        effects.append(("records", current_thread().name))
        if current_thread().name == rollback_on:
            raise _ExpectedRollback()
        return object(), admission, binding, identity

    def observe(active, pid):
        assert active is context and pid == child.host_pid
        effects.append(("peer", current_thread().name))
        return leader, child

    monkeypatch.setattr(controller, "_controller_records", records)
    monkeypatch.setattr(DaemonIdentityContext, "observe_pair", observe)
    monkeypatch.setattr(
        peer, "_credentials", lambda _connection: (child.host_pid, 65533, 65533)
    )

    def authenticate(factory):
        result = controller.authenticate_current_controller(
            factory,
            fence,
            object(),
            admission,
            binding=binding,
            supplied_ticket=ticket,
            observer_context=context,
        )
        assert type(result) is peer.ControllerPeer and result.admission == admission
        return result

    return deadline, authenticate


def _arrived(signal, task, detail):
    if not signal.wait(4):
        if task.done():
            task.result()
        pytest.fail(detail, pytrace=False)


@pytest.mark.parametrize(
    ("first", "finish"),
    [
        pytest.param("heartbeat", "commit", id="heartbeat-commits-first"),
        pytest.param("rpc", "commit", id="rpc-commits-first"),
        pytest.param("rpc", "rollback", id="rpc-rolls-back-first"),
    ],
)
def test_exact_workflow_rpc_coordinates_with_heartbeat(
    drill_databases, tmp_path, monkeypatch, first, finish
):
    if os.environ.get("MINEAGENT_WORKFLOW_HEARTBEAT_REQUIRED") != "1":
        pytest.skip("Explicit current native workflow-heartbeat proof required")

    import test_backup_restore as legacy

    from backend.app.rdagent_workflow_coordination import (
        controller_deadline,
        registered,
    )
    from backend.app.services import DomainError

    source = legacy._engine(drill_databases.source)
    try:
        legacy._upgrade(source, "032g_schema_keyset_cost")
        storage = tmp_path / "heartbeat-rpc-source"
        storage.mkdir(mode=0o700)
        with actual_environment(
            source, legacy._note_settings(source, storage), monkeypatch
        ) as env:
            fence, lease = _claimed_workflow(env)
            times_before = _lease_times(source, fence.attempt_id)
            first_name = "workflow-first_0"
            second_name = "workflow-contender_0"
            rpc_name = first_name if first == "rpc" else second_name
            effects = []
            deadline, authenticate = _controller_callback(
                fence,
                effects,
                first_name if finish == "rollback" else None,
                monkeypatch,
            )
            # A second factory targets the same real DB/attempt, but was not
            # enrolled by the trusted worker. It must retain one NOWAIT try.
            independent = sessionmaker(source, expire_on_commit=False)
            finishing, parked, release = Event(), Event(), Event()

            def at_db_finish(_connection):
                if current_thread().name == first_name:
                    # SQLAlchemy's engine event runs before its real DBAPI
                    # commit or rollback, so PostgreSQL still owns the lock.
                    assert not finishing.is_set(), "first SQL finish was replayed"
                    finishing.set()
                    assert release.wait(8), "fixture did not release DB finish"

            def pulse():
                lease.pulse(force=True)

            def rpc():
                with controller_deadline(env.factory, fence, deadline):
                    return authenticate(env.factory)

            first_call, second_call = (
                (pulse, rpc) if first == "heartbeat" else (rpc, pulse)
            )
            with registered(env.factory, fence) as gate:
                wait = gate._condition.wait

                def observed_wait(*args, **kwargs):
                    if current_thread().name == second_name:
                        parked.set()
                    return wait(*args, **kwargs)

                monkeypatch.setattr(gate._condition, "wait", observed_wait)
                event.listen(source, finish, at_db_finish)
                try:
                    with (
                        ThreadPoolExecutor(1, thread_name_prefix="workflow-first") as p,
                        ThreadPoolExecutor(
                            1, thread_name_prefix="workflow-contender"
                        ) as q,
                    ):
                        first_task = p.submit(first_call)
                        try:
                            _arrived(
                                finishing,
                                first_task,
                                "first transaction did not reach DB finish",
                            )
                            second_task = q.submit(second_call)
                            _arrived(
                                parked,
                                second_task,
                                "same-attempt contender did not park locally",
                            )
                            with pytest.raises(DomainError) as conflict:
                                authenticate(independent)
                            assert conflict.value.code == "neutral_busy"
                            assert not second_task.done()
                        finally:
                            release.set()
                        if finish == "rollback":
                            with pytest.raises(_ExpectedRollback):
                                first_task.result(timeout=10)
                        else:
                            first_task.result(timeout=10)
                        second_task.result(timeout=10)
                finally:
                    event.remove(source, finish, at_db_finish)

            assert effects == (
                [("records", rpc_name)]
                if finish == "rollback"
                else [("records", rpc_name), ("peer", rpc_name)]
            )
            times_after = _lease_times(source, fence.attempt_id)
            assert all(
                new > old for new, old in zip(times_after, times_before, strict=True)
            )
            assert not lease.lost.is_set() and not lease.cancelled.is_set()
            assert lease.thread.ident is None  # Exactly one deterministic pulse.
            assert env.rd_calls == env.oe_calls == []
    finally:
        source.dispose()
