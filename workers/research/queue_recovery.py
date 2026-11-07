"""Redeliver committed jobs, never restart an attempt or grant execution.

Recovery reads immutable receipts without consulting today's runtime/provider
availability. The normal task must still perform its own fresh claim and cannot
replace an existing attempt. Unknown or mixed schema revisions fail closed.
"""

from sqlalchemy import select, text

from backend.app.services import DomainError

# Exact installed successors, not lexical comparisons or assumed future support.
OE_REVISIONS = (
    "028_oe_structured_search",
    "029_research_workflow",
    "030_declared_upload_basket",
    "031_workflow_structured_stage",
    "032_normal_oe_note_pins",
    "032a_rd_terminal_results",
    "032b_oe_append_run_state",
    "032c_oe_provider_table_checks",
    "032d_rd_validation_cost",
)
RD_REVISIONS = (
    "023_rd_submissions",
    "024_openevolve_initial",
    "025_structured_factor_strategy",
    "026_rd_seeded_submissions",
    "027_rd_controller_v3",
    "027a_contract_repairs",
    "027b_provider_material",
    "027c_seeded_controller_v4",
    *OE_REVISIONS,
)


def _revision(session):
    revisions = session.scalars(text("SELECT version_num FROM alembic_version")).all()
    return revisions[0] if len(revisions) == 1 else None


def _fail():
    raise DomainError(
        "unsupported_queue_identity", "Exact committed queue receipt required", 409
    )


def recover_stalled_research_jobs(session):
    """Reconciler entry point; each supported family keeps its own task identity."""
    from workers.research.dispatcher import recover_stalled_jobs

    count = recover_stalled_jobs(session)
    revision = _revision(session)
    if revision in RD_REVISIONS:
        count += recover_stalled_jobs(session, rd=True)
    if revision in OE_REVISIONS:
        count += recover_stalled_jobs(session, oe=True)
    return count


def require_rd_receipt(session, run, generation):
    from backend.app.factor_analysis_models import FactorAnalysisPlan
    from backend.app.rdagent_models import FactorResearchSpec
    from backend.app.rdagent_seed_submission import validate_saved_seed_binding
    from backend.app.rdagent_submission_models import FactorResearchSubmission
    from backend.app.rdagent_submission_receipts import (
        validate_key,
        validate_saved_binding,
    )
    from workers.research.rdagent_queue import _queued_identity
    from workers.research.rdagent_seed_queue import seeded_row

    revision = _revision(session)
    if (
        revision not in RD_REVISIONS
        or run is None
        or type(generation) is not int
        or generation != 1
        or run.generation != 1
    ):
        _fail()
    validate_key(run.idempotency_key)
    try:
        config = _queued_identity(session, run)
    except (ValueError, TypeError, AttributeError):
        _fail()
    ordinary = session.scalar(
        select(FactorResearchSubmission).filter_by(owner_id=run.owner_id, run_id=run.id)
    )
    seeded = seeded_row(session, run, [revision])
    if config.schema_version == "mineagent.factor-research.v3":
        from backend.app.rdagent_workflow_models import FactorWorkflowSubmission
        from backend.app.rdagent_workflow_saved import (
            verify_workflow_submission_metadata,
        )

        row = session.scalar(
            select(FactorWorkflowSubmission).filter_by(
                owner_id=run.owner_id, run_id=run.id
            )
        )
        if row is None or ordinary is not None or seeded is not None:
            _fail()
        verified = verify_workflow_submission_metadata(session, run.owner_id, row.id)
        if verified.run_id != run.id or verified.config_sha256 != config.sha256:
            _fail()
    elif seeded is not None:
        if ordinary is not None:
            _fail()
        validate_saved_seed_binding(
            seeded,
            run,
            session.get(FactorResearchSpec, seeded.spec_id),
            session.get(FactorAnalysisPlan, seeded.analysis_plan_id),
            run.owner_id,
            run.idempotency_key,
        )
    elif ordinary is not None:
        # Handles both configured and provider requests without coercing either
        # into the historical RDSubmit schema.
        if ordinary.idempotency_key != run.idempotency_key:
            _fail()
        validate_saved_binding(
            ordinary,
            run,
            session.get(FactorAnalysisPlan, ordinary.plan_id),
            run.owner_id,
            run.idempotency_key,
        )
    else:
        _fail()


def require_oe_receipt(session, run, generation):
    from backend.app.oe_structured_identity import decode_spec
    from backend.app.oe_structured_models import OEStructuredSubmission
    from backend.app.oe_structured_submission_receipts import validate_key
    from packages.openevolve_structured.workflow_run import WorkflowStructuredRunConfig

    if (
        run is None
        or type(generation) is not int
        or generation != 1
        or _revision(session) not in OE_REVISIONS
    ):
        _fail()
    # Includes exact run/config/source/program identity and, for v2, the owned
    # workflow attachment. No queue args can select a different engine family.
    spec, config = decode_spec(session, run)
    row = session.get(OEStructuredSubmission, spec.submission_id)
    validate_key(run.idempotency_key)
    if row is None or (
        row.owner_id,
        row.run_id,
        row.idempotency_key,
        row.origin,
        row.request_canonical,
        row.request_sha256,
        row.preflight_sha256,
        row.config_sha256,
    ) != (
        run.owner_id,
        run.id,
        run.idempotency_key,
        "workflow_stage"
        if type(config) is WorkflowStructuredRunConfig
        else "authenticated_user_submission",
        spec.request_canonical,
        spec.request_sha256,
        spec.preflight_sha256,
        spec.config_sha256,
    ):
        _fail()
