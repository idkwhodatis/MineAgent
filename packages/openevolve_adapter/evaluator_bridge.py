"""Installed upstream evaluator callback; generated Python is never executed here."""

import hashlib
import os
import stat

from packages.openevolve_adapter import observer
from packages.openevolve_adapter.contracts import SearchSpec
from packages.openevolve_adapter.evaluator_protocol import (
    EvaluationGrant,
    EvaluationResponse,
)
from packages.openevolve_adapter.observer_transport import (
    bootstrap,
    canonical,
    host_event,
)
from packages.openevolve_adapter.runtime_profile import PROFILE, validate_source_bound


def evaluate(program_path):
    """Ask the fenced host to run its separately isolated native evaluator.

    The host resolves candidate, spec, windows, dataset and process profile from
    durable state; a path/string supplied by upstream never authorizes a launch.
    """
    from openevolve.evaluation_result import EvaluationResult

    with observer.control_guard("candidate_registration"):
        grant = EvaluationGrant.model_validate_json(
            canonical(observer.evaluation_ticket())
        )
        fd = os.open(program_path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("Native source must be a regular file")
            source = os.read(fd, PROFILE.maximum_source_bytes + 1)
        finally:
            os.close(fd)
        validate_source_bound(source)
        if hashlib.sha256(source).hexdigest() != grant.source_sha256:
            raise ValueError("Actual temporary evaluator source differs from grant")
        initial = bootstrap()
        spec = SearchSpec.model_validate_json(canonical(initial["spec"]))
        if (
            grant.owner_id != spec.owner_id
            or grant.run_id != spec.run_id
            or str(grant.epoch_id) != initial["epoch_id"]
        ):
            raise ValueError("Evaluation grant differs from admitted epoch")
        answer = EvaluationResponse.model_validate_json(
            canonical(
                host_event(
                    "execute_evaluation", {"grant": grant.model_dump(mode="json")}
                )
            )
        )
        from packages.research_runtime.broker_contracts import digest

        if (
            answer.candidate_id != grant.candidate_id
            or answer.source_sha256 != grant.source_sha256
            or answer.objective_sha256 != digest(spec.objective)
        ):
            raise ValueError("Host objective receipt scope differs")
        observer.record_evaluation_result(answer)
        # Empty metrics are only an internal transit value. The mandatory
        # outer evaluation_complete hook rejects ineligible before return to
        # the seed/worker and before native population insertion.
        metrics = {"combined_score": answer.combined_score} if answer.eligible else {}
        return EvaluationResult(
            metrics=metrics, artifacts={"native_feedback": answer.feedback}
        )
