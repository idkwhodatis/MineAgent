"""Closed handler routing with real wire/private peer codecs and handler doubles."""

import hashlib
from dataclasses import replace
from uuid import uuid4

import pytest

from backend.app import openevolve_operational_router as router
from packages.openevolve_adapter.checkpoint_native import canonical
from runtimes.openevolve_operational import peer as peers
from runtimes.openevolve_operational.wire import parse_event
from tests.openevolve.test_operational_peer import auth, case  # noqa:F401


def event(kind):
    value = {
        "schema_version": "mineagent.openevolve-event.v1",
        "kind": kind,
        "admission_id": str(uuid4()),
        "epoch_id": str(uuid4()),
        "payload": {},
    }
    return parse_event(
        canonical(
            {
                "event": value,
                "event_sha256": hashlib.sha256(canonical(value)).hexdigest(),
                "process_ticket": "a" * 64,
            }
        )
    )


@pytest.mark.parametrize(
    "kind,worker,module,name",
    [
        ("iteration_intent", False, "controller", "apply_controller_event"),
        ("terminal_applied", False, "controller", "apply_controller_event"),
        ("checkpoint_prepare", False, "controller", "prepare_controller_checkpoint"),
        ("seed_source", False, "candidates", "check_seed_source"),
        ("worker_started", True, "workers", "start_worker"),
        ("worker_outcome", True, "workers", "finish_worker"),
        ("rendered_prompt", True, "workers", "render_prompt"),
        ("proposal_source", True, "workers", "observe_proposal_source"),
        ("proposal_response", True, "workers", "observe_proposal_response"),
        ("candidate_intent", True, "candidates", "register_candidate"),
        ("evaluation_complete", True, "candidates", "acknowledge_evaluation"),
    ],
)
def test_closed_handler_receipt_only_after_return(
    case, monkeypatch, kind, worker, module, name
):  # noqa:F811
    if worker:
        monkeypatch.setattr(peers, "_credentials", lambda _: (102, 65533, 65533))
        peer = auth(case, binding=case.worker_binding, worker_ticket=case.worker_ticket)
    else:
        peer = auth(case)
    calls, receipt = [], uuid4()

    def handler(*args, **kwargs):
        calls.append((args, kwargs))
        return receipt, {"committed": True}

    monkeypatch.setattr(getattr(router, module), name, handler)
    observed = event(kind)
    result = router.dispatch_event(
        "factory",
        case.fence,
        peer,
        event=observed,
        ticket_key=b"k" * 32,
        settings="settings",
    )
    assert result == (receipt, {"committed": True})
    assert len(calls) == 1 and calls[0][1]["event"] is observed


@pytest.mark.parametrize(
    "kind", ["worker_started", "worker_outcome", "proposal_response", "rendered_prompt"]
)
def test_controller_cannot_impersonate_worker(case, kind):  # noqa:F811
    with pytest.raises(ValueError, match="assigned worker"):
        router.dispatch_event(
            None,
            case.fence,
            auth(case),
            event=event(kind),
            ticket_key=b"k" * 32,
            settings=None,
        )


def test_historical_unused_event_not_fake_ack(case):  # noqa:F811
    with pytest.raises(ValueError, match="no operational handler"):
        router.dispatch_event(
            None,
            case.fence,
            auth(case),
            event=event("candidate_outcome"),
            ticket_key=b"k" * 32,
            settings=None,
        )


def test_changed_event_rejected_before_handler(case):  # noqa:F811
    observed = replace(event("seed_source"), payload={"injected": True})
    with pytest.raises(ValueError):
        router.dispatch_event(
            None,
            case.fence,
            auth(case),
            event=observed,
            ticket_key=b"k" * 32,
            settings=None,
        )
