"""027 upstream callback: candidate JSON is data, never imported/executed here."""

import hashlib
from uuid import UUID

from packages.factors.contracts import digest
from runtimes.openevolve_operational.fixed_input import read_fixed

from . import observer
from .contracts import EvaluationGrant, EvaluationResponse, StructuredSearchSpec
from .observer_transport import bootstrap, canonical, host_event
from .runtime_profile import project_current


def evaluate(program_path):
    from openevolve.evaluation_result import EvaluationResult

    with observer.control_guard("candidate_registration"):
        grant = EvaluationGrant.model_validate_json(
            canonical(observer.evaluation_ticket())
        )
        raw = read_fixed(program_path, 32768)
        projected = project_current(raw)
        initial = bootstrap()
        spec = StructuredSearchSpec.model_validate_json(canonical(initial["spec"]))
        if grant.epoch_id != UUID(initial["epoch_id"]):
            raise ValueError("Evaluation epoch differs from current bootstrap")
        if (
            grant.owner_id,
            grant.run_id,
            grant.candidate_sha256,
            grant.program_sha256,
            grant.execution_binding_sha256,
        ) != (
            spec.owner_id,
            spec.run_id,
            hashlib.sha256(raw).hexdigest(),
            projected.program_sha256,
            spec.execution_binding_sha256,
        ):
            raise ValueError("Actual candidate differs from persisted evaluation grant")
        answer = EvaluationResponse.model_validate_json(
            canonical(
                host_event(
                    "execute_evaluation", {"grant": grant.model_dump(mode="json")}
                )
            )
        )
        if any(
            getattr(answer, key) != getattr(grant, key)
            for key in (
                "candidate_id",
                "candidate_sha256",
                "program_sha256",
                "execution_id",
                "execution_binding_sha256",
            )
        ) or answer.objective_sha256 != digest(spec.objective):
            raise ValueError("Actual structured objective receipt differs")
        observer.record_evaluation_result(answer)
        return EvaluationResult(
            metrics={"combined_score": answer.combined_score}
            if answer.eligible
            else {},
            artifacts={"structured_feedback": answer.feedback},
        )
