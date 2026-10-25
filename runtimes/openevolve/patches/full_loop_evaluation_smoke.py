"""Actual pinned API/spawn loop, with explicit synthetic observer/transport doubles.

NOT a financial evaluator, durable host, Docker containment or admission gate.
Only execute directly; this file never installs hooks when imported as a library.
"""

import argparse
import hashlib
import json
import multiprocessing
import os
import pickle
import re
import socket
import tempfile
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

SEED = "# EVOLVE-BLOCK-START\nprobe_value = 0\n# EVOLVE-BLOCK-END\n"
ACTIVE = ContextVar("full_loop_test_intent", default=None)
STACK = ContextVar("full_loop_test_stack", default=None)


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def record(kind, **payload):
    root = Path(os.environ["MINEAGENT_EXPLICIT_LOOP_PROBE"])
    with (root / f"events-{os.getpid()}.jsonl").open("a") as stream:
        stream.write(json.dumps({"kind": kind, "pid": os.getpid(), **payload}) + "\n")


def spec():
    from packages.openevolve_adapter.contracts import (
        DevelopmentWindow,
        InputFile,
        NoFactors,
        ObjectiveProfile,
        SearchSpec,
        SeedPreparation,
        SourceBinding,
        StrategyExecutionPlan,
    )
    from packages.research_runtime.budget_contracts import BudgetVector

    source = SourceBinding(
        strategy_version_id=uuid4(),
        source_sha256=hashlib.sha256(SEED.encode()).hexdigest(),
        manifest_sha256="a" * 64,
    )
    objective = ObjectiveProfile(
        windows=(
            DevelopmentWindow(
                id="dev",
                start=date(2020, 1, 1),
                end=date(2020, 2, 1),
                weight_millionths=1_000_000,
            ),
        )
    )
    plan = StrategyExecutionPlan(
        dataset_version_id=uuid4(),
        dataset_sha256="a" * 64,
        files=(InputFile(logical_name="probe", sha256="a" * 64, size_bytes=1),),
        **{
            key: "a" * 64
            for key in (
                "universe_sha256",
                "listing_map_sha256",
                "calendar_sha256",
                "price_semantics_sha256",
                "engine_config_sha256",
                "parameter_sha256",
                "state_schema_sha256",
                "capture_profile_sha256",
            )
        },
        evaluator_image="sha256:" + "a" * 64,
        market="US",
        warmup_sessions=0,
        rng_seed=42,
        windows=objective.windows,
    )
    return SearchSpec(
        owner_id=uuid4(),
        run_id=uuid4(),
        preparation=SeedPreparation(kind="none", original=source, prepared=source),
        execution=plan,
        objective=objective,
        factors=NoFactors(),
        runtime_image="sha256:" + "b" * 64,
        runtime_lock_sha256="c" * 64,
        adapter_patch_sha256="d" * 64,
        prompt_profile_sha256="e" * 64,
        budget=BudgetVector(
            calls=2,
            tokens=4096,
            mutation_slots=2,
            evaluator_executions=3,
            wall_milliseconds=600000,
            storage_bytes=8 * 1024 * 1024,
            process_slots=2,
        ),
        maximum_mutation_slots=2,
        population_size=8,
        archive_size=8,
    )


def fixture_diff_response(user, context):
    """Synthetic transport selects exact current code, never history/inspiration."""
    matches = re.findall(
        r"^# Current Program\n```python\n(.*?)\n```", user, re.MULTILINE | re.DOTALL
    )
    if (
        len(matches) != 1
        or hashlib.sha256(matches[0].encode()).hexdigest()
        != context["parent_source_sha256"]
    ):
        raise ValueError("Fixture current program differs from selected parent")
    previous = re.search(r"probe_value = (\d+)", matches[0])
    iteration = context["native_iteration"]
    if previous is None or type(iteration) is not int or iteration < 1:
        raise ValueError("Invalid fixture current program or iteration")
    return f"<<<<<<< SEARCH\nprobe_value = {previous.group(1)}\n=======\nprobe_value = {iteration}\n>>>>>>> REPLACE"


def install_explicit_doubles():
    from packages.openevolve_adapter import broker_client as broker
    from packages.openevolve_adapter import observer
    from packages.openevolve_adapter.checkpoint_native import (
        state_sha256,
        validate_directory,
    )
    from packages.openevolve_adapter.contracts import SearchOperationContext

    def no_connect(*_args, **_kwargs):
        record("network_attempt")
        raise AssertionError("External or local socket connection forbidden in probe")

    socket.socket.connect = no_connect
    socket.socket.connect_ex = no_connect

    def before_submit(iteration, parent_id, inspiration_ids, snapshot):
        record("submit", iteration=iteration)
        return {
            "iteration": iteration,
            "parent_id": parent_id,
            "parent_source_sha256": hashlib.sha256(
                snapshot["programs"][parent_id]["code"].encode()
            ).hexdigest(),
            "selection_sha256": digest(snapshot),
            "intent_id": str(uuid4()),
            "epoch_id": str(observer.require_trusted_population()),
        }

    @contextmanager
    def worker_scope(descriptor):
        token = ACTIVE.set(descriptor)
        with ExitStack() as stack, observer.epoch_control(UUID(descriptor["epoch_id"])):
            stack_token = STACK.set(stack)
            try:
                record("worker_start", iteration=descriptor["iteration"])
                yield
            finally:
                STACK.reset(stack_token)
                ACTIVE.reset(token)

    @contextmanager
    def candidate_scope(program_id, code):
        record(
            "candidate",
            program_id=program_id,
            source_sha256=hashlib.sha256(code.encode()).hexdigest(),
        )
        from packages.openevolve_adapter.evaluator_protocol import EvaluationResponse

        expected = float(re.search(r"probe_value = (\d+)", code).group(1))
        mode = os.environ.get("MINEAGENT_LOOP_REJECTION")
        eligible = mode != "seed" and not (
            mode in {"children", "fallback"} and expected > 0
        )
        response = EvaluationResponse(
            candidate_id=UUID(program_id),
            source_sha256=hashlib.sha256(code.encode()).hexdigest(),
            objective_sha256="a" * 64,
            objective_receipt_id=uuid4(),
            eligible=eligible,
            combined_score=expected if eligible else None,
            reason="eligible" if eligible else "native_undefined",
            feedback="synthetic probe only",
        )
        token = observer._EVALUATION.set(
            {"grant": {"explicit_test_grant": True}, "result": response}
        )
        try:
            yield
        finally:
            observer._EVALUATION.reset(token)

    original_evaluation_complete = observer.evaluation_complete

    def evaluation_complete(metrics):
        try:
            original_evaluation_complete(metrics)
        except observer.CandidateEvaluationRejected:
            record("evaluation_rejected")
            raise
        else:
            record("evaluation_verified")

    from packages.openevolve_adapter import observer_transport

    def host_event(kind, payload):
        assert kind == "evaluation_complete"
        return {
            "acknowledged": True,
            "objective_receipt_id": payload["objective_receipt_id"],
        }

    observer_transport.host_event = host_event

    @contextmanager
    def prompt_scope():
        yield

    def prompt_complete(system, user):
        selected = ACTIVE.get()
        request = {
            "kind": "chat",
            "model": "openevolve-fixture.v1",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_output_tokens": 32768,
            "temperature_millionths": 0,
        }
        context = SearchOperationContext(
            owner_id=UUID(int=1),
            run_id=UUID(int=2),
            epoch_id=UUID(selected["epoch_id"]),
            iteration_intent_id=UUID(selected["intent_id"]),
            native_iteration=selected["iteration"],
            operation_ordinal=0,
            parent_candidate_id=UUID(selected["parent_id"]),
            parent_source_sha256=selected["parent_source_sha256"],
            selection_snapshot_sha256=selected["selection_sha256"],
            rendered_context_sha256=digest({"system": system, "user": user}),
        )
        STACK.get().enter_context(
            broker.bind_operation(
                broker.OperationGrant(
                    UUID(int=3), context, digest(request), "a" * 64, "b" * 64
                )
            )
        )
        record("prompt", iteration=selected["iteration"])

    def exchange(payload):
        value = json.loads(payload)
        iteration = value["context"]["native_iteration"]
        user = value["request"]["messages"][1]["content"]
        # History/inspiration may precede current code in the real native prompt.
        content = fixture_diff_response(user, value["context"])
        record("model", iteration=iteration)
        return broker._canonical(
            {
                "schema_version": "mineagent.openevolve-broker-result.v1",
                "request_sha256": value["request_sha256"],
                "context_sha256": digest(value["context"]),
                "receipt_id": str(uuid4()),
                "content": content,
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 3,
                    "total_tokens": 13,
                },
            }
        )

    def terminal(k, outcome, state):
        assert state["metadata"]["last_iteration"] == k
        record("terminal", frontier=k, outcome=outcome)

    def before_checkpoint(k, state):
        root = Path(os.environ["MINEAGENT_EXPLICIT_LOOP_PROBE"])
        destination = root / ("checkpoint-" + str(uuid4()))
        destination.mkdir()
        return {"token": destination.name, "path": str(destination)}

    def after_checkpoint(token, k, state):
        root = Path(os.environ["MINEAGENT_EXPLICIT_LOOP_PROBE"])
        assert validate_directory(root / token) == state
        record("checkpoint", frontier=k, path=str(root / token))

    def before_load(path):
        state = validate_directory(path)
        return {
            "token": "explicit-test",
            "path": path,
            "frontier": state["metadata"]["last_iteration"],
            "state_sha256": state_sha256(state),
        }

    def after_load(descriptor, state):
        assert state_sha256(state) == descriptor["state_sha256"]
        record("load", frontier=descriptor["frontier"])

    observer.before_submit = before_submit
    observer.worker_scope = worker_scope
    observer.worker_outcome = lambda result: record(
        "worker_outcome", iteration=result.iteration, error=result.error
    )
    observer.candidate_scope = candidate_scope
    observer.evaluation_complete = evaluation_complete
    observer.assert_seed_source = lambda source: (
        source == SEED or (_ for _ in ()).throw(AssertionError("seed drift"))
    )
    observer.proposal_response = lambda response, usage: record("response")
    observer.proposal_source = lambda stage, source: record("source", stage=stage)
    observer.prompt_scope = prompt_scope
    observer.prompt_reference = lambda role, program: record("reference", role=role)
    observer.prompt_current_source = lambda source: None
    observer.prompt_artifacts = lambda section: None
    observer.prompt_complete = prompt_complete
    observer.terminal_applied = terminal
    observer.before_checkpoint = before_checkpoint
    observer.after_checkpoint = after_checkpoint
    observer.before_checkpoint_load = before_load
    observer.after_checkpoint_load = after_load
    broker._exchange = exchange


def events(root):
    return [
        json.loads(line)
        for path in root.glob("events-*.jsonl")
        for line in path.read_text().splitlines()
    ]


def main():
    from openevolve.api import run_evolution

    from packages.openevolve_adapter import observer
    from packages.openevolve_adapter.broker_client import init_client
    from packages.openevolve_adapter.runtime_config import build_config

    parser = argparse.ArgumentParser()
    parser.add_argument("--reject", choices=("seed", "children", "fallback"))
    args = parser.parse_args()
    multiprocessing.set_start_method("spawn")
    with tempfile.TemporaryDirectory(prefix="mineagent-loop-probe-") as temporary:
        root = Path(temporary)
        os.environ["MINEAGENT_EXPLICIT_LOOP_PROBE"] = str(root)
        if args.reject:
            os.environ["MINEAGENT_LOOP_REJECTION"] = args.reject
        install_explicit_doubles()
        seed = root / "seed.py"
        seed.write_text(SEED)
        evaluator = root / "probe_evaluator.py"
        evaluator.write_text("""from pathlib import Path
import re
def evaluate(program_path):
    # Synthetic source-reading hook probe, not strategy execution or financial score.
    value = int(re.search(r"probe_value = (\\d+)", Path(program_path).read_text()).group(1))
    if REJECT_MODE == "fallback" and value > 0:
        return {"error": 0.0}
    if REJECT_MODE == "seed" or (REJECT_MODE == "children" and value > 0):
        return {}
    return {"combined_score": float(value)}
""")
        evaluator.write_text(
            "REJECT_MODE = " + repr(args.reject) + "\n" + evaluator.read_text()
        )
        config = build_config(spec(), SEED.encode())
        assert (
            pickle.loads(pickle.dumps(config)).llm.models[0].init_client is init_client
        )
        try:
            with observer.epoch_control(uuid4()):
                result = run_evolution(
                    seed,
                    evaluator,
                    config=config,
                    iterations=2,
                    output_dir=str(root / "fresh"),
                    cleanup=False,
                )
        except observer.SearchControlAbort as error:
            if args.reject == "fallback":
                assert error.phase == "candidate_registration"
                rejected = events(root)
                assert [
                    (e["frontier"], e["outcome"])
                    for e in rejected
                    if e["kind"] == "terminal"
                ] == [(0, "seed")]
                print(
                    json.dumps(
                        {
                            "mode": "fallback_refused",
                            "last_frontier": 0,
                            "scope": "synthetic objective verification seam only",
                        }
                    )
                )
                return
            assert args.reject == "seed" and error.phase == "seed_admission"
            rejected = events(root)
            assert len([e for e in rejected if e["kind"] == "evaluation_rejected"]) == 1
            assert not any(
                e["kind"] in {"submit", "checkpoint", "model", "terminal"}
                for e in rejected
            )
            print(
                json.dumps(
                    {
                        "mode": "seed_rejected",
                        "model_calls": 0,
                        "checkpoints": 0,
                        "scope": "synthetic objective verification seam only",
                    }
                )
            )
            return
        first = events(root)
        if args.reject == "children":
            assert result.best_score == 0.0
            assert [
                (e["frontier"], e["outcome"]) for e in first if e["kind"] == "terminal"
            ] == [(0, "seed"), (1, "error"), (2, "error")]
            assert len([e for e in first if e["kind"] == "evaluation_rejected"]) == 2
            assert all(
                e["error"] is not None for e in first if e["kind"] == "worker_outcome"
            )
            from packages.openevolve_adapter.checkpoint_native import validate_directory

            final_checkpoint = [e for e in first if e["kind"] == "checkpoint"][-1]
            assert len(validate_directory(final_checkpoint["path"])["programs"]) == 1
            assert not any(e["kind"] == "network_attempt" for e in first)
            print(
                json.dumps(
                    {
                        "mode": "children_rejected",
                        "model_calls": 2,
                        "frontier": 2,
                        "population_count": 1,
                        "scope": "synthetic objective verification seam only",
                    }
                )
            )
            return
        assert result.best_score == 2.0
        assert [
            (e["frontier"], e["outcome"]) for e in first if e["kind"] == "terminal"
        ] == [(0, "seed"), (1, "accepted"), (2, "accepted")]
        assert len([e for e in first if e["kind"] == "model"]) == 2
        assert all(e["pid"] != os.getpid() for e in first if e["kind"] == "model")
        checkpoint = [e for e in first if e["kind"] == "checkpoint"][-1]["path"]
        with observer.epoch_control(uuid4()):
            continued = run_evolution(
                seed,
                evaluator,
                config=config,
                iterations=1,
                output_dir=str(root / "continued"),
                cleanup=False,
                checkpoint_path=checkpoint,
            )
        all_events = events(root)
        assert continued.best_score == 3.0
        assert len([e for e in all_events if e["kind"] == "evaluation_verified"]) == 4
        assert any(e["kind"] == "load" and e["frontier"] == 2 for e in all_events)
        assert len([e for e in all_events if e["kind"] == "model"]) == 3
        assert len([e for e in all_events if e["kind"] == "candidate"]) == 4
        assert len({e["pid"] for e in all_events if e["kind"] == "model"}) == 2
        assert not any(e["kind"] == "network_attempt" for e in all_events)
        print(
            json.dumps(
                {
                    "actual_api": "run_evolution",
                    "start_method": "spawn",
                    "fresh_mutations": 2,
                    "continuation_mutations": 1,
                    "model_calls": 3,
                    "candidate_scopes": 4,
                    "network_attempts": 0,
                    "scope": "synthetic observer/transport/evaluator probe only; no durable/financial/containment acceptance",
                },
                sort_keys=True,
            )
        )


if __name__ == "__mp_main__":
    install_explicit_doubles()
if __name__ == "__main__":
    main()
