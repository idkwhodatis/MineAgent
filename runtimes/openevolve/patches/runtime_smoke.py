"""Exercise installed patched spawn and executor context, not strategy evaluation."""

from __future__ import annotations

import asyncio
import multiprocessing as mp
import os
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from types import SimpleNamespace

from openevolve.config import Config
from openevolve.evaluator import Evaluator
from openevolve.process_parallel import ProcessParallelController

TICKET = ContextVar("patch_smoke_ticket")


def observe_ticket(path: str, *, label: str):
    return TICKET.get(), path, label


async def context_proof():
    evaluator = Evaluator.__new__(Evaluator)
    evaluator.config = SimpleNamespace(timeout=3)
    evaluator._evaluator_kwargs = {"label": "context-only"}
    evaluator.evaluate_function = observe_ticket
    with ThreadPoolExecutor(max_workers=1) as executor:
        evaluator._executor = executor
        token = TICKET.set("immutable-test-ticket")
        try:
            observed = await evaluator._direct_evaluate("not-a-program")
        finally:
            TICKET.reset(token)
    assert observed == ("immutable-test-ticket", "not-a-program", "context-only")


def main():
    # The caller must establish spawn before controller construction.
    config = Config()
    config.evaluator.parallel_evaluations = 1
    config.max_tasks_per_child = None
    if mp.get_start_method(allow_none=True) is None:
        try:
            ProcessParallelController(config, __file__, None)
        except RuntimeError as exc:
            assert "spawn before" in str(exc)
        else:
            raise AssertionError("Uninitialized multiprocessing policy was accepted")
        mp.set_start_method("spawn")
    controller = ProcessParallelController(config, __file__, None)
    controller.start()
    try:
        child = controller.executor.submit(os.getpid).result(timeout=15)
        assert child != os.getpid()
    finally:
        controller.stop()
    config.evaluator.parallel_evaluations = 2
    try:
        ProcessParallelController(config, __file__, None)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Multiple workers were accepted")
    asyncio.run(context_proof())
    print(
        "Installed patched spawn and executor-context checks passed; no search/evaluator acceptance claimed."
    )


if __name__ == "__main__":
    main()
