"""Real PostgreSQL-authorized Docker units, not bypassed fixture capabilities."""

import json
import socket
import time
from uuid import uuid4

import pytest
from sqlalchemy import select

from backend.app.research_lifecycle import neutral_transaction
from backend.app.research_process_models import (
    ResearchProcessReceipt,
    ResearchProcessState,
)
from backend.app.research_process_services import mark_uncertain
from packages.research_runtime.process_contracts import ProcessError
from runtimes.research_supervisor.ipc import accept_fixture_peer, authenticate_peer
from runtimes.research_supervisor.supervisor import (
    DockerSupervisor,
    assert_quiescent,
    operation_path,
)
from tests.integration.test_research_process_recovery import (
    _admit_fixture_process,
    _required_image,
)
from tests.integration.test_research_process_recovery import (
    process_fence_fixture as shared_process_fence_fixture,
)
from workers.research.process_recovery import recover_unit, run_unit

process_fence_fixture = shared_process_fence_fixture

pytestmark = pytest.mark.postgres


@pytest.fixture
def real_unit(process_fence_fixture, settings):
    factory, fence = process_fence_fixture
    admission, ticket = _admit_fixture_process(
        factory, fence, _required_image(), settings=settings
    )
    return factory, fence, settings.storage_root, admission, ticket


def test_real_whole_unit_child_survives_controller_then_verified_stop(real_unit):
    factory, fence, root, admission, ticket = real_unit
    with run_unit(factory, fence, root, admission.id, ticket) as unit:
        with pytest.raises(ProcessError):
            assert_quiescent(root)
        peer = accept_fixture_peer(root, admission, unit.identity)
        assert peer.admission_id == admission.id
        state = unit.supervisor._inspect(unit.identity["container_id"])
        assert state["State"]["Running"] is True
        assert state["HostConfig"]["NetworkMode"] == "none"
        with factory() as session:
            assert session.get(ResearchProcessState, admission.id).status == "running"
            assert (
                session.scalar(
                    select(ResearchProcessReceipt).where(
                        ResearchProcessReceipt.admission_id == admission.id,
                        ResearchProcessReceipt.kind == "launch_intent",
                    )
                )
                is not None
            )
    assert unit.supervisor._inspect(unit.identity["container_id"]) is None
    assert_quiescent(root)


def test_real_peer_authentication_rejects_outside_pid_and_wrong_ticket(real_unit):
    factory, fence, root, admission, ticket = real_unit
    with run_unit(factory, fence, root, admission.id, ticket) as unit:
        peer = accept_fixture_peer(root, admission, unit.identity)
        left, right = socket.socketpair()
        with left, right:
            with pytest.raises(ProcessError):
                authenticate_peer(left, admission, unit.identity, "f" * 64)
            with pytest.raises(ProcessError):
                authenticate_peer(left, admission, unit.identity, ticket)
        assert peer.admission_id == admission.id


@pytest.mark.parametrize("lost_response", ["create", "start"])
def test_real_uncertain_create_is_recovered_by_exact_journal(
    real_unit, monkeypatch, lost_response
):
    factory, fence, root, admission, ticket = real_unit
    original = DockerSupervisor._command

    def lost(self, args, *, check=True):
        result = original(self, args, check=check)
        if args[0] == lost_response:
            raise ProcessError("research_process_uncertain")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(DockerSupervisor, "_command", lost)
        with (
            pytest.raises(ProcessError),
            run_unit(factory, fence, root, admission.id, ticket),
        ):
            pytest.fail("Lost create/start response acknowledged")
    assert_quiescent(root)
    with (
        pytest.raises(ProcessError, match="already_admitted"),
        run_unit(factory, fence, root, admission.id, ticket),
    ):
        pytest.fail("Consumed intent authorized a second unit")


def test_real_tampered_journal_never_authorizes_stop(real_unit):
    factory, fence, root, admission, ticket = real_unit
    with run_unit(factory, fence, root, admission.id, ticket) as unit:
        journal = operation_path(root, admission) / "control/launch.json"
        original = journal.read_bytes()
        try:
            from packages.research_runtime.process_contracts import canonical

            value = json.loads(original)
            value["admission"]["run_id"] = str(uuid4())
            journal.write_bytes(canonical(value))
            with pytest.raises(ProcessError):
                unit.supervisor.stop_and_verify(root, admission)
            assert unit.supervisor._inspect(unit.identity["container_id"])["State"][
                "Running"
            ]
        finally:
            journal.write_bytes(original)


def test_real_host_deadline_stops_still_running_fixed_fixture(real_unit):
    factory, fence, root, admission, ticket = real_unit
    with run_unit(factory, fence, root, admission.id, ticket) as unit:
        accept_fixture_peer(root, admission, unit.identity)
        assert unit.supervisor._inspect(unit.identity["container_id"])["State"][
            "Running"
        ]
        assert unit.watchdog.expired.wait(65), (
            "Trusted host did not enforce its profile deadline"
        )
        unit.watchdog.thread.join(45)
        assert unit.watchdog.error is None
        assert unit.supervisor._inspect(unit.identity["container_id"]) is None
    assert_quiescent(root)


def _owned_host_caller(database_url, schema, fence, root, admission_id, ticket, pipe):
    import re

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    assert re.fullmatch(r"tradegrad_test_[0-9a-f]{32}", schema)
    engine = create_engine(
        database_url,
        connect_args={
            "options": f"-csearch_path={schema} -cstatement_timeout=10000",
            "connect_timeout": 5,
        },
    )
    try:
        with run_unit(
            sessionmaker(engine, expire_on_commit=False),
            fence,
            root,
            admission_id,
            ticket,
        ) as unit:
            pipe.send(unit.identity["container_id"])
            while True:
                time.sleep(1)
    finally:
        engine.dispose()


def test_real_pid1_deadline_survives_host_caller_death(real_unit):
    import multiprocessing

    factory, fence, root, admission, ticket = real_unit
    from sqlalchemy import text

    with factory() as session:
        schema = session.scalar(text("SELECT current_schema()"))
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(
        target=_owned_host_caller,
        args=(
            factory.kw["bind"].url.render_as_string(hide_password=False),
            schema,
            fence,
            root,
            admission.id,
            ticket,
            child,
        ),
    )
    process.start()
    supervisor = DockerSupervisor(admission.image_id)
    try:
        assert parent.poll(30), (
            "Authoritative worker did not acknowledge committed launch"
        )
        container = parent.recv()
        process.kill()
        process.join(10)
        assert not process.is_alive()
        assert supervisor._inspect(container)["State"]["Running"]
        deadline = time.monotonic() + 70
        while time.monotonic() < deadline:
            state = supervisor._inspect(container)
            if state is not None and not state["State"]["Running"]:
                break
            time.sleep(0.2)
        assert state["State"]["Running"] is False
        assert state["State"]["ExitCode"] == 124
    finally:
        if process.is_alive():
            process.kill()
            process.join(10)
        parent.close()
        child.close()
        recover_unit(factory, root, admission.id)
    assert_quiescent(root)


def test_real_live_peer_cannot_submit_after_process_projection_uncertain(
    process_fence_fixture, settings
):
    from backend.app.research_broker_models import ResearchModelTransport
    from backend.app.research_broker_services import initialize_budget
    from packages.research_runtime.broker_contracts import digest
    from packages.research_runtime.budget_contracts import BudgetVector
    from packages.research_runtime.control import BrokerAbort
    from tests.integration.test_model_broker_accounting import seed_operation
    from workers.research.model_broker import execute_fixture_once

    factory, fence = process_fence_fixture
    (settings.storage_root / "research-objects").mkdir(exist_ok=True)
    with neutral_transaction(factory, fence) as session:
        initialize_budget(
            session,
            fence,
            BudgetVector(
                calls=10,
                tokens=10000,
                storage_bytes=16 * 1024**2,
                process_slots=2,
                wall_milliseconds=120000,
            ),
        )
    operation_id, _, request, _ = seed_operation(factory, fence, settings)
    admission, ticket = _admit_fixture_process(
        factory, fence, _required_image(), logical_id=operation_id, settings=settings
    )
    with run_unit(factory, fence, settings.storage_root, admission.id, ticket) as unit:
        peer = accept_fixture_peer(settings.storage_root, admission, unit.identity)
        with neutral_transaction(factory, fence, resource_scope="processes") as session:
            mark_uncertain(session, fence, admission)
        with pytest.raises(BrokerAbort):
            execute_fixture_once(
                factory,
                fence,
                operation_id,
                digest(request),
                authorization=peer,
                settings=settings,
            )
        with factory() as session:
            assert (
                session.scalar(
                    select(ResearchModelTransport.id).where(
                        ResearchModelTransport.operation_id == operation_id
                    )
                )
                is None
            )


def test_real_recovery_manifest_rejects_unsafe_projection_drift(real_unit):
    from copy import deepcopy

    from runtimes.research_supervisor.supervisor import load_launch

    factory, fence, root, admission, ticket = real_unit
    with run_unit(factory, fence, root, admission.id, ticket) as unit:
        operation = operation_path(root, admission)
        launch = load_launch(operation, admission, root)
        state = unit.supervisor._inspect(unit.identity["container_id"])
        assert unit.supervisor._verify(state, launch, operation)
        for section, field, value in [
            ("HostConfig", "PidMode", "host"),
            ("HostConfig", "IpcMode", "host"),
            ("HostConfig", "ShmSize", 64 * 1024**2),
            ("HostConfig", "Devices", [{"PathOnHost": "/dev/kvm"}]),
            ("HostConfig", "SecurityOpt", ["no-new-privileges", "seccomp=unconfined"]),
            ("Config", "Entrypoint", ["sh"]),
            ("Config", "Cmd", ["-c", "sleep 600"]),
            ("Config", "Env", [*state["Config"]["Env"], "DATABASE_URL=forged"]),
        ]:
            corrupt = deepcopy(state)
            corrupt[section][field] = value
            with pytest.raises(ProcessError):
                unit.supervisor._verify(corrupt, launch, operation)
        corrupt = deepcopy(state)
        bind = next(m for m in corrupt["Mounts"] if m["Type"] == "bind")
        bind["Source"] = str(root / "unrelated-owner" / str(admission.id) / "ipc")
        with pytest.raises(ProcessError):
            unit.supervisor._verify(corrupt, launch, operation)
