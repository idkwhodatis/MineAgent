from dataclasses import replace
from threading import Event, Thread
from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy.orm import sessionmaker

from backend.app import rdagent_workflow_coordination as coord
from backend.app.services import DomainError, FenceLost
from packages.rdagent_provider.process_receipts import SDKStopped
from packages.research_runtime.fences import AttemptFence


@pytest.fixture
def identities():
    return sessionmaker(), AttemptFence(uuid4(), uuid4(), uuid4(), 1, uuid4())


def test_exact_factory_and_full_attempt_only(identities):
    factory, fence = identities
    with coord.registered(factory, fence), coord.neutral(factory, fence):
        # Same thread, other scope is transparent, not reentrant.
        with (
            coord.neutral(sessionmaker(), fence),
            coord.neutral(factory, replace(fence, lease_token=uuid4())),
            coord.neutral(factory, replace(fence, generation=2)),
            coord.neutral(factory, replace(fence, attempt_id=uuid4())),
            coord.neutral(factory, replace(fence, run_id=uuid4())),
            coord.neutral(factory, replace(fence, owner_id=uuid4())),
        ):
            pass
        with (
            pytest.raises(DomainError, match="Neutral execution authority"),
            coord.neutral(factory, fence),
        ):
            pytest.fail("nested hold must not enter")
    assert coord._lookup(factory, fence) is None
    with coord.neutral(factory, fence):
        pass


def test_registration_does_not_replace_live_binding(identities):
    factory, fence = identities
    with coord.registered(factory, fence) as gate:
        with (
            pytest.raises(FenceLost),
            coord.registered(factory, replace(fence, generation=2)),
        ):
            pytest.fail("duplicate binding must not enter")
        assert coord._lookup(factory, fence) is gate


def test_original_controller_deadline_rejects_before_body_but_not_cleanup(identities):
    factory, fence = identities
    with coord.registered(factory, fence):
        with coord.controller_deadline(factory, fence, monotonic() - 1):
            with pytest.raises(DomainError) as error, coord.neutral(factory, fence):
                pytest.fail("expired request entered transaction")
            assert error.value.code == "neutral_busy"
        with coord.neutral(factory, fence):
            pass  # RPC error cleanup is not under the expired RPC deadline.


@pytest.mark.parametrize("heartbeat_first", [True, False])
@pytest.mark.parametrize("rolls_back", [True, False])
def test_busy_lane_waits_for_full_holder_exit_once(
    monkeypatch, identities, heartbeat_first, rolls_back
):
    factory, fence = identities
    events = []
    parked = Event()
    finished = Event()
    errors = []

    class Rollback(Exception):
        pass

    def transaction(heartbeat):
        if heartbeat:
            return coord.heartbeat(factory, fence, deadline=monotonic() + 3)
        return coord.neutral(factory, fence)

    def contender():
        try:
            with transaction(not heartbeat_first):
                events.append("contender")
        except (DomainError, FenceLost) as error:
            errors.append(error)
        finally:
            finished.set()

    with coord.registered(factory, fence) as gate:
        wait = gate._condition.wait

        def observe_wait(timeout):
            parked.set()
            return wait(timeout)

        monkeypatch.setattr(gate._condition, "wait", observe_wait)
        worker = Thread(target=contender, daemon=True)
        try:
            with transaction(heartbeat_first):
                worker.start()
                assert parked.wait(1)
                assert not finished.is_set()
                events.append("commit" if not rolls_back else "rollback")
                if rolls_back:
                    raise Rollback()
        except Rollback:
            pass
        finally:
            worker.join(timeout=3)
        assert not worker.is_alive()
        assert not errors
        assert events == ["rollback" if rolls_back else "commit", "contender"]


def test_expired_heartbeat_never_enters_body(identities):
    factory, fence = identities
    with coord.registered(factory, fence):
        with (
            pytest.raises(FenceLost),
            coord.heartbeat(factory, fence, deadline=monotonic() - 1),
        ):
            pytest.fail("expired heartbeat entered transaction")
        with coord.neutral(factory, fence):
            pass


def test_positive_stop_has_one_cutoff_and_original_scope_is_restored(
    identities, monkeypatch
):
    factory, fence = identities
    now = [100.0]
    monkeypatch.setattr(coord, "monotonic", lambda: now[0])
    monkeypatch.setattr(coord, "monotonic_ns", lambda: int(now[0] * 1e9))
    stop = SDKStopped(
        owner_id=fence.owner_id,
        run_id=fence.run_id,
        attempt_id=fence.attempt_id,
        generation=fence.generation,
        transport_id=uuid4(),
        request_sha256="a" * 64,
        model_binding_sha256="b" * 64,
        identity={
            "pid": 2,
            "start_ticks": 3,
            "process_group": 2,
            "session_id": 2,
            "parent_pid": 1,
            "boot_id": uuid4(),
            "deadline_monotonic_ns": 1,
        },
        observed_monotonic_ns=100_000_000_000,
        leader_reaped=True,
        process_group_absent=True,
    )
    with (
        coord.registered(factory, fence),
        coord.controller_deadline(factory, fence, 99),
    ):
        for invalid in (
            object(),
            stop.model_copy(update={"process_group_absent": False}),
            stop.model_copy(update={"owner_id": uuid4()}),
        ):
            with (
                pytest.raises(DomainError),
                coord._positive_sdk_stop_admission(factory, fence, invalid),
            ):
                pytest.fail("invalid evidence admitted")
        with coord._positive_sdk_stop_admission(factory, fence, stop):
            with coord.neutral(factory, fence):
                pass
            with (
                pytest.raises(DomainError),
                coord._positive_sdk_stop_admission(factory, fence, stop),
            ):
                pytest.fail("nested context renewed admission")
            now[0] += coord.OWNER_WAIT_SECONDS
            with pytest.raises(DomainError), coord.neutral(factory, fence):
                pytest.fail("stop retention minted another admission window")
        with pytest.raises(DomainError), coord.neutral(factory, fence):
            pytest.fail("ordinary RPC admission inherited stop-only scope")
        with (
            pytest.raises(DomainError),
            coord._positive_sdk_stop_admission(factory, fence, stop),
        ):
            pytest.fail("a separate scope renewed an expired stop observation")
