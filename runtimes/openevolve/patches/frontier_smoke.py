"""Installed native method probes only: no durable host/evaluator acceptance."""

import asyncio
import json
import tempfile
import threading
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from openevolve.config import Config
from openevolve.controller import OpenEvolve
from openevolve.database import Program, ProgramDatabase
from openevolve.process_parallel import ProcessParallelController, SerializableResult

from packages.openevolve_adapter import observer
from packages.openevolve_adapter.checkpoint_native import snapshot, validate_directory


def database():
    config = Config()
    config.database.num_islands = 1
    config.database.in_memory = True
    config.database.log_prompts = False
    db = ProgramDatabase(config.database)
    db.add(
        Program(id=str(uuid4()), code="probe = 0\n", metrics={"combined_score": -5.0})
    )
    return config, db


def prove_frontier(
    *,
    early=False,
    out_of_order=False,
    mutation_failure=False,
    publication_failure=False,
):
    config, db = database()
    value = ProcessParallelController.__new__(ProcessParallelController)
    value.config = config
    value.num_workers = value.num_islands = 1
    value.island_selector = None
    value.shutdown_event = threading.Event()
    value.executor = object()
    value.database = db
    value.early_stopping_triggered = False
    value.evolution_tracer = None
    submissions, checkpoints, terminals = [], [], []
    native_add = db.add

    def add(program, **kwargs):
        if mutation_failure:
            db.programs[program.id] = program
            raise RuntimeError("after insertion")
        if kwargs.get("iteration") == 3:
            return None
        return native_add(program, **kwargs)

    db.add = add

    def submit(iteration, _island):
        submissions.append(iteration)
        future = Future()
        if out_of_order and iteration == 1:
            return future
        if iteration == 1 and not early and not mutation_failure:
            result = SerializableResult(
                iteration=iteration, error="ordinary parse failure"
            )
        else:
            child = Program(
                id=str(uuid4()),
                code=f"probe = {iteration}\n",
                metrics={"combined_score": -4.0},
            )
            result = SerializableResult(
                iteration=iteration, child_program_dict=child.to_dict(), target_island=0
            )
        future.set_result(result)
        return future

    def terminal(iteration, outcome, state):
        assert state["metadata"]["last_iteration"] == iteration
        terminals.append((iteration, outcome))

    def checkpoint(k):
        if publication_failure:
            raise RuntimeError("publication")
        checkpoints.append(k)
        assert submissions == [1, 2] if k == 1 else True

    value._submit_iteration = submit
    with (
        patch.object(observer, "terminal_applied", terminal),
        observer.epoch_control(uuid4()) as epoch,
    ):
        try:
            asyncio.run(
                value.run_evolution(
                    1,
                    3,
                    target_score=-4.0 if early else None,
                    checkpoint_callback=checkpoint,
                )
            )
        except observer.SearchControlAbort:
            assert out_of_order or mutation_failure or publication_failure
            assert not epoch.population_state_trusted
            assert submissions == [1, 2]
            assert checkpoints == []
        else:
            assert not (out_of_order or mutation_failure or publication_failure)
            assert checkpoints == ([1] if early else [1, 2, 3])
            assert terminals == (
                [(1, "accepted")]
                if early
                else [(1, "error"), (2, "accepted"), (3, "rejected")]
            )
            assert db.last_iteration == value.processed_frontier == checkpoints[-1]


def prove_native_roundtrip():
    config, db = database()
    native = OpenEvolve.__new__(OpenEvolve)
    native.database = db
    native.file_extension = ".py"
    with tempfile.TemporaryDirectory() as temp:
        destination = Path(temp) / "native"
        destination.mkdir()
        callbacks = []
        with (
            observer.epoch_control(uuid4()),
            patch.object(
                observer,
                "before_checkpoint",
                lambda k, s: {"token": "test", "path": str(destination)},
            ),
            patch.object(
                observer, "after_checkpoint", lambda *args: callbacks.append(args)
            ),
        ):
            native._save_checkpoint(0)
        expected = validate_directory(destination)
        assert expected == snapshot(db) and len(callbacks) == 1
        restored = ProgramDatabase(config.database)
        restored.load(str(destination))
        assert snapshot(restored) == expected
        # Exercise the real controller loader and reject semantic reconstruction drift.
        for drift in (False, True):
            loaded = OpenEvolve.__new__(OpenEvolve)
            loaded.database = ProgramDatabase(config.database)
            original_load = loaded.database.load

            def load(
                path, original_load=original_load, drift=drift, db=loaded.database
            ):
                original_load(path)
                if drift:
                    db.island_generations[0] += 1

            loaded.database.load = load
            load_acks = []
            descriptor = {
                "token": "test",
                "path": str(destination),
                "frontier": 0,
                "state_sha256": "test",
            }
            with (
                observer.epoch_control(uuid4()),
                patch.object(
                    observer,
                    "before_checkpoint_load",
                    lambda _path, descriptor=descriptor: descriptor,
                ),
                patch.object(
                    observer,
                    "after_checkpoint_load",
                    lambda *args, load_acks=load_acks: load_acks.append(args),
                ),
            ):
                try:
                    loaded._load_checkpoint(str(destination))
                except observer.SearchControlAbort as error:
                    assert drift and error.phase == "checkpoint_load"
                    assert not load_acks
                else:
                    assert not drift and len(load_acks) == 1
        # Native loader would filter this unknown key; strict validator rejects it.
        program_file = next((destination / "programs").iterdir())
        original = program_file.read_bytes()
        bad = json.loads(original)
        bad["silently_filtered"] = True
        program_file.write_text(json.dumps(bad))
        try:
            validate_directory(destination)
        except ValueError:
            pass
        else:
            raise AssertionError("Unknown native field accepted")
        program_file.write_bytes(original)
        (destination / "unexpected").write_text("extra")
        try:
            validate_directory(destination)
        except ValueError:
            pass
        else:
            raise AssertionError("Extra native file accepted")


def prove_seed_load_cleanup():
    for resume in (False, True):
        config, _populated = database()
        native = OpenEvolve.__new__(OpenEvolve)
        native.config = config
        native.database = ProgramDatabase(config.database)
        native.parallel_controller = None
        closed = []
        native.evolution_tracer = SimpleNamespace(
            close=lambda closed=closed: closed.append(True)
        )
        native.initial_program_code = "probe = 0\n"
        native.evaluator = SimpleNamespace()

        def fail(*args):
            raise RuntimeError("injected seed/load failure")

        with (
            observer.epoch_control(uuid4()),
            patch.object(observer, "assert_seed_source", fail),
            patch.object(observer, "before_checkpoint_load", fail),
        ):
            try:
                asyncio.run(
                    native.run(
                        iterations=1, checkpoint_path="missing" if resume else None
                    )
                )
            except observer.SearchControlAbort as error:
                assert error.phase == (
                    "checkpoint_load" if resume else "seed_admission"
                )
            else:
                raise AssertionError("Seed/load failure continued")
        assert closed == [True]
        assert not native.database.programs


def prove_final_frontier():
    native = OpenEvolve.__new__(OpenEvolve)
    native.config, native.database = database()
    saved = []

    async def run(*args, **kwargs):
        return None

    native.parallel_controller = SimpleNamespace(
        run_evolution=run,
        processed_frontier=2,
        shutdown_event=threading.Event(),
        early_stopping_triggered=True,
    )
    native._save_checkpoint = saved.append
    asyncio.run(native._run_evolution_with_checkpoints(1, 100, None))
    assert saved == [2], "Final checkpoint used configured maximum"


def main():
    for options in (
        {},
        {"early": True},
        {"out_of_order": True},
        {"mutation_failure": True},
        {"publication_failure": True},
    ):
        prove_frontier(**options)
    prove_native_roundtrip()
    prove_seed_load_cleanup()
    prove_final_frontier()
    print(
        "Installed contiguous-frontier, fail-stop and native roundtrip probes passed; no durable search acceptance."
    )


if __name__ == "__main__":
    main()
