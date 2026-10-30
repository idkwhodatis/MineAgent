"""Closed retained-state projection, never provider/code/identity contents."""

import json

from sqlalchemy import select


def report_normal_rd_state(session, run):
    from backend.app.models import ExecutionAttempt
    from backend.app.rdagent_models import (
        FactorEvaluationOutcome,
        FactorGenerationSource,
    )
    from backend.app.research_broker_models import ResearchModelOperation
    from backend.app.research_process_models import ResearchProcessReceipt

    try:
        profile = run.config.get(
            "controller_profile", "mineagent.rdagent-orchestrator.v2"
        )
        allowed_profiles = {
            "mineagent.rdagent-orchestrator.v2",
            "mineagent.rdagent-orchestrator.v3",
            "mineagent.rdagent-orchestrator.v4",
        }
        status = (
            run.status
            if run.status in {"queued", "running", "failed", "succeeded", "cancelled"}
            else "unknown"
        )
        attempt = session.get(ExecutionAttempt, run.active_attempt_id)
        result = {
            "profile": profile if profile in allowed_profiles else "unknown",
            "status": status,
            "sandbox_stopped": attempt is not None and attempt.sandbox_stopped is True,
        }
        for label, model in (
            ("sources", FactorGenerationSource),
            ("evaluations", FactorEvaluationOutcome),
            ("model_operations", ResearchModelOperation),
        ):
            result[label] = len(
                session.scalars(
                    select(model.id).filter_by(run_id=run.id).limit(33)
                ).all()
            )
        kinds = session.scalars(
            select(ResearchProcessReceipt.kind).filter_by(run_id=run.id).limit(65)
        ).all()
        result["stop_receipt"] = "stop" in kinds
    except Exception:  # noqa: BLE001 - diagnostics cannot replace acceptance failure
        result = {"status": "diagnostic_unavailable"}
    print("NORMAL_RD_STATE_V1:" + json.dumps(result, sort_keys=True), flush=True)
