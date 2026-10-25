"""Installed upstream callback-seam tests with an explicit in-memory recorder.

No host persistence, model provider, strategy execution or financial score claim.
"""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from types import SimpleNamespace
from unittest.mock import patch

from openevolve import process_parallel as native
from openevolve.config import Config
from openevolve.controller import OpenEvolve
from openevolve.database import Program
from openevolve.evaluator import Evaluator

from packages.openevolve_adapter import observer
from packages.openevolve_adapter.observer import SearchControlAbort

ACTIVE = ContextVar("observer_seam_scope", default=None)
PARENT = "# EVOLVE-BLOCK-START\nvalue = 1\n# EVOLVE-BLOCK-END\n"


async def probe_evaluation(*_args):
    # A hook probe, deliberately not an objective/financial metric.
    return {"hook_probe": 1.0}


def exercise_worker(response):
    events = []
    config = Config()
    config.enforce_evolve_blocks = True
    config.diff_based_evolution = True
    config.prompt.programs_as_changes_description = False
    parent = Program(id="parent", code=PARENT)
    snapshot = {
        "programs": {parent.id: parent.to_dict()},
        "artifacts": {},
        "current_island": 0,
        "islands": [[parent.id]],
        "feature_dimensions": [],
        "sampling_island": 0,
    }

    @contextmanager
    def scope(descriptor):
        assert descriptor == {"test_grant": "explicit"}
        token = ACTIVE.set("worker")
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")
            ACTIVE.reset(token)

    def lazy():
        assert ACTIVE.get() == "worker"
        events.append("lazy")

    async def generate(**_kwargs):
        assert ACTIVE.get() == "worker"
        return response

    def observe_response(raw, _usage):
        assert raw == response
        events.append("response")

    def observe_source(stage, code):
        assert ACTIVE.get() == "worker"
        events.append((stage, code))

    def outcome(result):
        assert ACTIVE.get() == "worker"
        events.append(("outcome", bool(result.error)))

    with (
        patch.object(observer, "worker_scope", scope, create=True),
        patch.object(observer, "worker_outcome", outcome, create=True),
        patch.object(observer, "proposal_response", observe_response, create=True),
        patch.object(observer, "proposal_source", observe_source, create=True),
        patch.object(native, "_lazy_init_worker_components", lazy),
        patch.object(native, "_worker_config", config, create=True),
        patch.object(
            native,
            "_worker_prompt_sampler",
            SimpleNamespace(
                build_prompt=lambda **_k: {"system": "probe", "user": "probe"}
            ),
            create=True,
        ),
        patch.object(
            native,
            "_worker_llm_ensemble",
            SimpleNamespace(generate_with_context=generate),
            create=True,
        ),
        patch.object(
            native,
            "_worker_evaluator",
            SimpleNamespace(
                evaluate_program=probe_evaluation,
                get_pending_artifacts=lambda _id: None,
            ),
            create=True,
        ),
    ):
        result = native._run_iteration_worker(
            1, snapshot, "parent", [], {"test_grant": "explicit"}
        )
    assert events[:2] == ["enter", "lazy"]
    assert events[-1] == "exit"
    assert (
        sum(isinstance(event, tuple) and event[0] == "outcome" for event in events) == 1
    )
    assert "response" in events
    return result, events


def candidate_wrapper_proof():
    events = []

    @contextmanager
    def scope(program_id, code):
        assert (program_id, code) == ("probe-id", "not executed")
        token = ACTIVE.set("candidate")
        events.append("candidate-enter")
        try:
            yield
        finally:
            events.append("candidate-exit")
            ACTIVE.reset(token)

    async def implementation(code, program_id):
        assert ACTIVE.get() == "candidate"
        events.append("implementation")
        return {"hook_probe": 1.0}

    value = Evaluator.__new__(Evaluator)
    value._evaluate_program_impl = implementation
    with patch.object(observer, "candidate_scope", scope, create=True):
        asyncio.run(value.evaluate_program("not executed", "probe-id"))
    assert events == ["candidate-enter", "implementation", "candidate-exit"]


def submission_snapshot_proof():
    value = native.ProcessParallelController.__new__(native.ProcessParallelController)
    value.config = Config()
    parent = Program(id="selected-parent", code=PARENT)
    inspiration = Program(id="selected-inspiration", code=PARENT)
    snapshot = {"programs": {parent.id: parent.to_dict()}, "sampling_island": 0}
    value.database = SimpleNamespace(
        current_island=0, sample_from_island=lambda **_k: (parent, [inspiration])
    )
    value._create_database_snapshot = lambda: snapshot
    submitted = []
    value.executor = SimpleNamespace(
        submit=lambda *args: submitted.append(args) or "future"
    )

    def before(iteration, parent_id, inspiration_ids, observed):
        assert (iteration, parent_id, inspiration_ids) == (
            1,
            parent.id,
            [inspiration.id],
        )
        observed["programs"].clear()
        return {"test_grant": "fixed"}

    with patch.object(observer, "before_submit", before, create=True):
        assert value._submit_iteration(1, 0) == "future"
    assert parent.id in snapshot["programs"], "Observer mutated native snapshot"
    assert submitted[0][-1] == {"test_grant": "fixed"}


def seed_assertion_precedes_evaluation():
    value = OpenEvolve.__new__(OpenEvolve)
    value.config = Config()
    value.database = SimpleNamespace(last_iteration=0, programs={})
    value.initial_program_code = "wrong source"
    called = []

    async def evaluate(*args):
        called.append(args)
        raise AssertionError("Unadmitted seed reached evaluator")

    value.evaluator = SimpleNamespace(evaluate_program=evaluate)

    def reject(code):
        assert code == "wrong source"
        raise ValueError("Seed digest mismatch")

    with patch.object(observer, "assert_seed_source", reject, create=True):
        try:
            asyncio.run(value.run(iterations=1))
        except SearchControlAbort as error:
            assert error.phase == "seed_admission"
        else:
            raise AssertionError("Mismatched seed was accepted")
    assert not called


def main():
    malformed, _ = exercise_worker("not a diff")
    assert malformed.error
    missing, missing_events = exercise_worker(
        "<<<<<<< SEARCH\nmissing = 1\n=======\nmissing = 2\n>>>>>>> REPLACE"
    )
    assert missing.error
    assert ("applied", PARENT) in missing_events
    valid, events = exercise_worker(
        "<<<<<<< SEARCH\nvalue = 1\n=======\nvalue = 2\n>>>>>>> REPLACE"
    )
    assert not valid.error
    effective = PARENT.replace("value = 1", "value = 2")
    assert ("applied", effective) in events and ("enforced", effective) in events
    candidate_wrapper_proof()
    submission_snapshot_proof()
    seed_assertion_precedes_evaluation()
    print(
        "Installed lifecycle callback seam probes passed; recorder-only, no durable host/search acceptance."
    )


if __name__ == "__main__":
    main()
