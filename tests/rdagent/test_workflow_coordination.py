from dataclasses import replace
from threading import Event, Thread
from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy.orm import sessionmaker

from backend.app import rdagent_workflow_coordination as coord
from backend.app.services import DomainError, FenceLost
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
