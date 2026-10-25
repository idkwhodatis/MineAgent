"""Fault-inject installed native controller methods; not a search/evaluator gate."""

import asyncio
import threading
from concurrent.futures import Future
from types import SimpleNamespace
from uuid import uuid4

from openevolve.config import Config
from openevolve.database import Program
from openevolve.process_parallel import ProcessParallelController, SerializableResult

from packages.openevolve_adapter.observer import SearchControlAbort, epoch_control


def failure(*_args, **_kwargs):
    raise RuntimeError("injected native mutation failure")


def controller(database):
    value = ProcessParallelController.__new__(ProcessParallelController)
    value.config = Config()
    value.config.database.num_islands = 1
    value.config.evaluator.parallel_evaluations = 1
    value.config.checkpoint_interval = 1
    value.num_workers = value.num_islands = 1
    value.island_selector = None
    value.shutdown_event = threading.Event()
    value.executor = object()
    value.database = database
    value.early_stopping_triggered = False
    value.evolution_tracer = None
    return value


def prove_result_fail_stop(*, rejected_checkpoint=False, timeout=False):
    database = SimpleNamespace(
        add=(lambda *_a, **_k: None) if rejected_checkpoint else failure,
        log_island_status=lambda: None,
    )
    value = controller(database)
    submissions = []
    checkpoints = []

    def submit(iteration, _island):
        submissions.append(iteration)
        future = Future()
        if timeout:
            future.set_exception(TimeoutError("unconfirmed underlying work"))
        else:
            program = Program(
                id=str(uuid4()), code="not executed", metrics={"combined_score": -5.0}
            )
            future.set_result(
                SerializableResult(
                    child_program_dict=program.to_dict(), iteration=iteration
                )
            )
        return future

    def checkpoint(iteration):
        checkpoints.append(iteration)
        raise RuntimeError("injected publication failure")

    value._submit_iteration = submit
    with epoch_control(uuid4()) as epoch:
        try:
            asyncio.run(value.run_evolution(1, 3, checkpoint_callback=checkpoint))
        except SearchControlAbort as error:
            expected = (
                "evaluation_timeout"
                if timeout
                else "checkpoint_publication"
                if rejected_checkpoint
                else "result_application"
            )
            assert error.phase == expected
        else:
            raise AssertionError("Controller continued after unknown failure")
        assert not epoch.population_state_trusted
    assert submissions == [1, 2], "A replacement was submitted after failure"
    assert checkpoints == ([1] if rejected_checkpoint else [])


def prove_submission_fail_stop():
    value = controller(SimpleNamespace(current_island=0, sample_from_island=failure))
    with epoch_control(uuid4()) as epoch:
        try:
            value._submit_iteration(1, 0)
        except SearchControlAbort as error:
            assert error.phase == "submission"
        else:
            raise AssertionError("Sampling failure was silently skipped")
        assert not epoch.population_state_trusted


def main():
    prove_result_fail_stop()
    prove_result_fail_stop(rejected_checkpoint=True)
    prove_result_fail_stop(timeout=True)
    prove_submission_fail_stop()
    print(
        "Installed native controller fail-stop fault checks passed; no durable observer/search acceptance claimed."
    )


if __name__ == "__main__":
    main()
