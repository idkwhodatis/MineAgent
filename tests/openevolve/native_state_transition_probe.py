"""Actual native terminal transitions with explicit synthetic host callbacks.

Wraps the existing full API/spawn probe; never changes native algorithms or state.
Run normally and with --reject children. No financial/durable host acceptance.
"""

import copy
import json

from packages.openevolve_adapter import observer
from packages.openevolve_adapter.checkpoint_native import canonical
from runtimes.openevolve.patches import full_loop_evaluation_smoke as loop

ORIGINAL_INSTALL = loop.install_explicit_doubles
PREVIOUS = None
CHECKS = []


def install_transition_checks():
    ORIGINAL_INSTALL()
    original_terminal = observer.terminal_applied
    original_load = observer.after_checkpoint_load

    def terminal(frontier, outcome, state):
        global PREVIOUS
        surviving = 0
        if PREVIOUS is not None:
            old_programs = PREVIOUS["programs"]
            for pid in old_programs.keys() & state["programs"].keys():
                if canonical(old_programs[pid]) != canonical(state["programs"][pid]):
                    changed = [
                        key
                        for key in old_programs[pid]
                        if canonical(old_programs[pid][key])
                        != canonical(state["programs"][pid][key])
                    ]
                    raise AssertionError(
                        {
                            "frontier": frontier,
                            "outcome": outcome,
                            "surviving_program_changed_fields": changed,
                        }
                    )
                surviving += 1
            if outcome in {"error", "rejected"}:
                expected = copy.deepcopy(PREVIOUS)
                expected["metadata"]["last_iteration"] = frontier
                if canonical(state) != canonical(expected):
                    differences = {
                        key: {
                            "before": expected["metadata"][key],
                            "after": state["metadata"][key],
                        }
                        for key in expected["metadata"]
                        if canonical(expected["metadata"][key])
                        != canonical(state["metadata"][key])
                    }
                    print(
                        json.dumps(
                            {
                                "native_failure_transition_mismatch": differences,
                                "frontier": frontier,
                                "outcome": outcome,
                            }
                        ),
                        flush=True,
                    )
                assert canonical(state) == canonical(expected), {
                    "frontier": frontier,
                    "outcome": outcome,
                    "failure_state_changed": True,
                }
        else:
            assert frontier == 0 and outcome == "seed"
        original_terminal(frontier, outcome, state)
        PREVIOUS = copy.deepcopy(state)
        CHECKS.append(
            {
                "frontier": frontier,
                "outcome": outcome,
                "unchanged_surviving_programs": surviving,
                "full_failure_state_unchanged": outcome in {"error", "rejected"},
            }
        )

    def after_load(descriptor, state):
        global PREVIOUS
        original_load(descriptor, state)
        if PREVIOUS is not None:
            assert canonical(PREVIOUS) == canonical(state), (
                "Continuation changed prior terminal snapshot"
            )
        PREVIOUS = copy.deepcopy(state)

    observer.terminal_applied = terminal
    observer.after_checkpoint_load = after_load


loop.install_explicit_doubles = install_transition_checks

if __name__ == "__mp_main__":
    install_transition_checks()
if __name__ == "__main__":
    loop.main()
    print(
        json.dumps(
            {
                "schema": "mineagent.native-state-transition-probe.v1",
                "checks": CHECKS,
                "scope": "actual installed phase6 API with synthetic host/transport/evaluator; no durable financial acceptance",
            },
            sort_keys=True,
        )
    )
