"""Installed real bridge/candidate/receipt gate probe with explicit host doubles.

No physical strategy execution, financial receipt or host durability acceptance.
"""

import asyncio
import hashlib
import tempfile
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from openevolve.evaluation_result import EvaluationResult
from openevolve.evaluator import Evaluator

from packages.openevolve_adapter import evaluator_bridge as bridge
from packages.openevolve_adapter import observer, observer_transport
from packages.openevolve_adapter.evaluator_protocol import (
    EvaluationGrant,
    EvaluationResponse,
)
from packages.openevolve_adapter.runtime_config import build_config
from packages.research_runtime.broker_contracts import digest
from runtimes.openevolve.patches.full_loop_smoke import SEED, spec


def exercise(mode):
    specification = spec()
    epoch, program, candidate = uuid4(), uuid4(), uuid4()
    events = []
    with tempfile.TemporaryDirectory(prefix="bridge-probe-") as temporary:
        root = Path(temporary)
        sentinel = root / "candidate-was-executed"
        source = f'from pathlib import Path\nPath({str(sentinel)!r}).write_text("unexpected")\nraise RuntimeError("candidate must never execute in bridge")\n'
        source_sha = hashlib.sha256(source.encode()).hexdigest()
        source_file = root / "candidate.py"
        source_file.write_text(source)
        grant = EvaluationGrant(
            owner_id=specification.owner_id,
            run_id=specification.run_id,
            epoch_id=epoch,
            candidate_id=candidate,
            upstream_program_id=program,
            source_sha256=source_sha,
            ticket="a" * 64,
            phase="seed",
        )
        response = EvaluationResponse(
            candidate_id=candidate,
            source_sha256=source_sha,
            objective_sha256=digest(specification.objective),
            objective_receipt_id=uuid4(),
            eligible=True,
            combined_score=-2.5,
            reason="eligible",
            feedback="explicit synthetic feedback",
        )
        initial = {
            "spec": specification.model_dump(mode="json"),
            "epoch_id": str(epoch),
        }

        def host_event(kind, payload):
            events.append(kind)
            if kind == "candidate_intent":
                ticket = grant.model_dump(mode="json")
                if mode == "wrong_grant":
                    ticket["run_id"] = str(uuid4())
                return ticket
            if kind == "execute_evaluation":
                answer = response.model_dump(mode="json")
                if mode == "wrong_receipt":
                    answer["candidate_id"] = str(uuid4())
                return answer
            if kind == "evaluation_complete":
                return {
                    "acknowledged": True,
                    "objective_receipt_id": str(response.objective_receipt_id),
                }
            raise AssertionError("Unexpected host operation")

        with (
            patch.object(observer_transport, "bootstrap", lambda: initial),
            patch.object(observer_transport, "host_event", host_event),
            patch.object(bridge, "bootstrap", lambda: initial),
            patch.object(bridge, "host_event", host_event),
            observer.epoch_control(epoch) as control,
        ):
            try:
                if mode == "loader":
                    config = build_config(specification, SEED.encode())
                    # Real upstream file-loader, actual evaluate_program wrapper,
                    # copied ContextVar into its real ThreadPoolExecutor.
                    evaluator = Evaluator(config.evaluator, str(Path(bridge.__file__)))
                    metrics = asyncio.run(
                        evaluator.evaluate_program(source, str(program))
                    )
                    assert metrics == {"combined_score": -2.5}
                    assert evaluator.get_pending_artifacts(str(program)) == {
                        "native_feedback": response.feedback
                    }
                else:
                    with observer.candidate_scope(str(program), source):
                        if mode == "mutated":
                            source_file.write_text(source + "# changed\n")
                        elif mode == "symlink":
                            alias = root / "alias.py"
                            alias.symlink_to(source_file)
                            source_file = alias
                        result = bridge.evaluate(str(source_file))
                        assert type(result) is EvaluationResult
                        assert result.metrics == {"combined_score": -2.5}
                        assert result.artifacts == {
                            "native_feedback": response.feedback
                        }
                        if mode == "duplicate":
                            bridge.evaluate(str(source_file))
                        observer.evaluation_complete(
                            {"combined_score": 0.0}
                            if mode == "score_drift"
                            else result.metrics
                        )
            except observer.SearchControlAbort as error:
                assert mode in {
                    "mutated",
                    "symlink",
                    "wrong_grant",
                    "wrong_receipt",
                    "duplicate",
                    "score_drift",
                }
                assert error.phase == "candidate_registration"
                assert not control.population_state_trusted
                assert "evaluation_complete" not in events
                if mode in {"mutated", "symlink", "wrong_grant"}:
                    assert "execute_evaluation" not in events
            else:
                assert mode in {"valid", "loader"}
                assert events == [
                    "candidate_intent",
                    "execute_evaluation",
                    "evaluation_complete",
                ]
        assert not sentinel.exists(), "Candidate Python was imported or executed"
        assert observer._EVALUATION.get() is None


def main():
    modes = (
        "valid",
        "loader",
        "mutated",
        "symlink",
        "wrong_grant",
        "wrong_receipt",
        "duplicate",
        "score_drift",
    )
    for mode in modes:
        exercise(mode)
    print(
        "Installed bridge probes passed: real file loader, thread context, EvaluationResult, source/grant/receipt/drift rejection, no candidate execution; synthetic host only."
    )


if __name__ == "__main__":
    main()
