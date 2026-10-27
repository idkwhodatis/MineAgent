"""Actual native controller payload -> closed DTO, with synthetic host only."""

import hashlib
import json
from uuid import uuid4

from packages.openevolve_adapter import observer, sampling_control
from packages.openevolve_adapter.checkpoint_native import state_sha256
from packages.openevolve_adapter.event_projection import (
    evidence_projections,
    parse_controller_event,
)
from packages.openevolve_adapter.observer_transport import canonical
from runtimes.openevolve_phase7 import full_loop_sampling_probe as phase7

COUNT = {"iteration_intent": 0, "terminal_applied": 0}
INSTALL = phase7.install


def parse_actual(kind, payload):
    event = {
        "schema_version": "mineagent.openevolve-event.v1",
        "admission_id": str(uuid4()),
        "epoch_id": str(observer.require_trusted_population()),
        "kind": kind,
        "payload": payload,
    }
    raw = canonical(
        {
            "event": event,
            "event_sha256": hashlib.sha256(canonical(event)).hexdigest(),
            "process_ticket": "a" * 64,
        }
    )
    result = parse_controller_event(raw)
    for projection in evidence_projections(result):
        assert len(projection.content) <= 1024 * 1024
    COUNT[kind] += 1


def install():
    INSTALL()
    old_host, old_terminal = sampling_control.host_event, observer.terminal_applied

    def host(kind, payload):
        parse_actual(kind, payload)
        return old_host(kind, payload)

    def terminal(iteration, outcome, state):
        parse_actual(
            "terminal_applied",
            {
                "iteration": iteration,
                "outcome": outcome,
                "native_state": state,
                "state_sha256": state_sha256(state),
            },
        )
        return old_terminal(iteration, outcome, state)

    sampling_control.host_event = host
    observer.terminal_applied = terminal


phase7.loop.install_explicit_doubles = install
phase7.install = install
if __name__ == "__mp_main__":
    install()
if __name__ == "__main__":
    phase7.prove_loop(2)
    assert COUNT == {"iteration_intent": 2, "terminal_applied": 3}
    print(
        json.dumps(
            {
                "actual_controller_projection_counts": COUNT,
                "scope": "Synthetic host; no authentication, SQL or containment acceptance",
            },
            sort_keys=True,
        )
    )
