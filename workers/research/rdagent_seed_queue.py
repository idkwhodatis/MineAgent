"""026 seeded receipt dispatch; callers retain the normal owner/run claim lock.

No alternate task, fake attempt or detached parent authority. The shared worker
creates the real attempt only after this immutable family and current physical
parent/data preflight succeeds.
"""

from sqlalchemy import select

from backend.app.factor_analysis_models import FactorAnalysisPlan
from backend.app.rdagent_models import FactorResearchSpec
from backend.app.rdagent_seed_models import FactorSeededSubmission
from backend.app.rdagent_seed_preflight import resolve_seeded_runtime, seeded_preflight
from backend.app.rdagent_seed_services import read_saved_seed
from backend.app.rdagent_seed_submission import validate_saved_seed_binding
from backend.app.services import DomainError


def seeded_row(session, run, revisions):
    if revisions not in (
        ["026_rd_seeded_submissions"],
        ["027_rd_controller_v3"],
        ["027a_contract_repairs"],
        ["027b_provider_material"],
        ["027c_seeded_controller_v4"],
    ):
        return None
    return session.scalar(
        select(FactorSeededSubmission).filter_by(owner_id=run.owner_id, run_id=run.id)
    )


def require_seeded_queue_binding(session, run, row, config, settings, *, read_inputs):
    if config.schema_version == "mineagent.factor-research.v2":
        from workers.research.rdagent_provider_queue import (
            require_provider_queue_binding,
        )

        return require_provider_queue_binding(
            session, run, row, config, settings, read_inputs=read_inputs
        )
    plan = session.get(FactorAnalysisPlan, row.analysis_plan_id)
    validate_saved_seed_binding(
        row,
        run,
        session.get(FactorResearchSpec, row.spec_id),
        plan,
        run.owner_id,
        run.idempotency_key,
    )
    profile = resolve_seeded_runtime(settings)
    if (
        profile.runtime != config.runtime
        or profile.qlib_installed_parent_image_id != plan.qlib_installed_parent_image_id
    ):
        raise DomainError(
            "runtime_profile_changed",
            "Queued seed runtime differs from server profile",
            409,
        )
    if read_inputs:
        request, _parent = read_saved_seed(row)
        checked = seeded_preflight(session, run.owner_id, request, settings)
        if not checked.can_submit or checked.preflight_sha256 != row.preflight_sha256:
            raise DomainError(
                "preflight_changed",
                "Queued seed parent/data differs from declaration",
                409,
            )
