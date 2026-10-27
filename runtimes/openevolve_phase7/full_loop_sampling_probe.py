"""Actual phase7 native sampling/API loop with explicitly synthetic host receipts.

Captures exact sampling transitions separately from K; never exempts arbitrary
feature-stat changes during terminal application. No durable host acceptance.
"""

import copy
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from packages.openevolve_adapter import observer, sampling_control
from packages.openevolve_adapter.checkpoint_native import canonical, state_sha256
from runtimes.openevolve.patches import full_loop_evaluation_smoke as loop

REAL_SAMPLING = sampling_control.before_submit
ORIGINAL_INSTALL = loop.install_explicit_doubles
CURSOR = None
SEQUENCE = 0
CHECKS = []


def install():
    ORIGINAL_INSTALL()
    old_submit, old_terminal, old_load = (
        observer.before_submit,
        observer.terminal_applied,
        observer.after_checkpoint_load,
    )

    def sampled(iteration, parent_id, inspiration_ids, snapshot, before, after):
        global CURSOR, SEQUENCE
        assert CURSOR is not None and canonical(before) == canonical(CURSOR)
        assert (
            before["metadata"]["last_iteration"] == after["metadata"]["last_iteration"]
        )
        assert canonical(snapshot["programs"]) == canonical(after["programs"])
        assert canonical(before["programs"]) == canonical(after["programs"])
        descriptor = REAL_SAMPLING(
            iteration, parent_id, inspiration_ids, snapshot, before, after
        )
        SEQUENCE += 1
        CHECKS.append(
            {
                "event": "sampling_ack",
                "control_sequence": SEQUENCE,
                "iteration": iteration,
                "frontier": after["metadata"]["last_iteration"],
                "before_sha256": state_sha256(before),
                "after_sha256": state_sha256(after),
                "state_changed": before != after,
            }
        )
        CURSOR = copy.deepcopy(after)
        return descriptor

    def terminal(frontier, outcome, state):
        global CURSOR, SEQUENCE
        if CURSOR is not None:
            for pid in CURSOR["programs"].keys() & state["programs"].keys():
                assert canonical(CURSOR["programs"][pid]) == canonical(
                    state["programs"][pid]
                )
            if outcome in {"error", "rejected"}:
                expected = copy.deepcopy(CURSOR)
                expected["metadata"]["last_iteration"] = frontier
                assert canonical(expected) == canonical(state), (
                    "Unacknowledged terminal state mutation"
                )
        old_terminal(frontier, outcome, state)
        CURSOR = copy.deepcopy(state)
        SEQUENCE += 1
        CHECKS.append(
            {
                "event": "terminal_ack",
                "control_sequence": SEQUENCE,
                "frontier": frontier,
                "outcome": outcome,
            }
        )

    def loaded(descriptor, state):
        global CURSOR
        old_load(descriptor, state)
        CURSOR = copy.deepcopy(state)

    def host_sampling(kind, payload):
        assert kind == "iteration_intent"
        assert payload["sampling"]["before_state_sha256"] == state_sha256(CURSOR)
        from packages.openevolve_adapter.sampling_contracts import (
            validate_sampling_transition,
        )

        validate_sampling_transition(
            CURSOR,
            payload["sampling"]["after_state"],
            payload["parent_id"],
            payload["snapshot"],
        )
        descriptor = old_submit(
            payload["iteration"],
            payload["parent_id"],
            payload["inspiration_ids"],
            payload["snapshot"],
        )
        descriptor.update(
            schema_version="mineagent.openevolve-iteration-grant.v1",
            native_iteration=payload["iteration"],
        )
        return {
            "iteration_grant": descriptor,
            "sampling_receipt_id": str(uuid4()),
            "control_sequence": SEQUENCE + 1,
            "state_sha256": state_sha256(payload["sampling"]["after_state"]),
        }

    sampling_control.host_event = host_sampling
    sampling_control.before_submit = sampled
    observer.terminal_applied = terminal
    observer.after_checkpoint_load = loaded


def prove_no_dispatch_without_ack():
    from types import SimpleNamespace

    from openevolve.config import Config
    from openevolve.database import Program, ProgramDatabase
    from openevolve.process_parallel import ProcessParallelController

    config = Config()
    config.database.num_islands = 1
    config.database.in_memory = True
    database = ProgramDatabase(config.database)
    database.add(
        Program(id=str(uuid4()), code="probe=0\n", metrics={"combined_score": 0.0})
    )
    native = ProcessParallelController.__new__(ProcessParallelController)
    native.config, native.database = config, database
    calls = []
    native.executor = SimpleNamespace(submit=lambda *args: calls.append(args))
    before = state_sha256(
        __import__(
            "packages.openevolve_adapter.checkpoint_native", fromlist=["snapshot"]
        ).snapshot(database)
    )

    def refuse(kind, payload):
        assert payload["sampling"]["before_state_sha256"] != state_sha256(
            payload["sampling"]["after_state"]
        ), "Actual native sampling mutation was not observed"
        raise RuntimeError("Synthetic host refused sampling ACK")

    with (
        patch.object(sampling_control, "host_event", refuse),
        observer.epoch_control(uuid4()) as epoch,
    ):
        try:
            native._submit_iteration(1, 0)
        except observer.SearchControlAbort as error:
            assert error.phase == "submission" and not epoch.population_state_trusted
        else:
            raise AssertionError("Failed sampling ACK allowed dispatch")
    assert not calls
    return before


def prove_loop(count, reject=False):
    import multiprocessing

    from openevolve.api import run_evolution

    from packages.openevolve_adapter.runtime_config import build_config

    multiprocessing.set_start_method("spawn")
    with tempfile.TemporaryDirectory(prefix="sampling-zero-") as temporary:
        os.environ["MINEAGENT_EXPLICIT_LOOP_PROBE"] = temporary
        if reject:
            os.environ["MINEAGENT_LOOP_REJECTION"] = "children"
        install()
        root = Path(temporary)
        seed, evaluator = root / "seed.py", root / "evaluate.py"
        seed.write_text(loop.SEED)
        evaluator.write_text(
            'from pathlib import Path\nimport re\ndef evaluate(path):\n    value = int(re.search(r"probe_value = (\\d+)", Path(path).read_text()).group(1))\n    return {} if '
            + repr(reject)
            + ' and value > 0 else {"combined_score": float(value)}\n'
        )
        with observer.epoch_control(uuid4()):
            result = run_evolution(
                seed,
                evaluator,
                config=build_config(loop.spec(), loop.SEED.encode()),
                iterations=count,
                output_dir=str(root / "out"),
                cleanup=False,
            )
        assert result.best_score == (0.0 if reject else float(count))
        sampling = [event for event in CHECKS if event["event"] == "sampling_ack"]
        terminals = [event for event in CHECKS if event["event"] == "terminal_ack"]
        assert len(sampling) == count
        assert [event["frontier"] for event in sampling] == [0] * count
        assert [event["frontier"] for event in terminals] == list(range(count + 1))
        assert [event["outcome"] for event in terminals] == ["seed"] + [
            "error" if reject else "accepted"
        ] * count
        assert not any(
            event["kind"] == "network_attempt" for event in loop.events(root)
        )


loop.install_explicit_doubles = install
if __name__ == "__mp_main__":
    install()
if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["--zero"]:
        prove_loop(0)
    elif sys.argv[1:] == ["--ack-failure"]:
        prove_no_dispatch_without_ack()
    else:
        assert sys.argv[1:] in ([], ["--reject", "children"])
        prove_loop(2, reject=bool(sys.argv[1:]))
    print(
        json.dumps(
            {
                "schema": "mineagent.sampling-state-probe.v1",
                "checks": CHECKS,
                "scope": "actual phase7 native API with synthetic sampling ACK; no durable/financial acceptance",
            },
            sort_keys=True,
        )
    )
