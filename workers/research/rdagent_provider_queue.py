"""Fresh real worker checks for frozen model/steer declarations, not hot edits."""


from backend.app.factor_analysis_models import FactorAnalysisPlan
from backend.app.rdagent_models import FactorResearchSpec
from backend.app.rdagent_provider_preflight import provider_preflight
from backend.app.rdagent_provider_profile import (
    resolve_provider_declaration,
    resolve_provider_runtime,
)
from backend.app.rdagent_seed_models import FactorSeededSubmission
from backend.app.rdagent_seed_services import read_saved_seed
from backend.app.rdagent_seed_submission import validate_saved_seed_binding
from backend.app.rdagent_steer_services import verify_steer_snapshot
from backend.app.rdagent_submission_receipts import read_request, validate_saved_binding
from backend.app.services import DomainError


def require_provider_queue_binding(session, run, row, config, settings, *, read_inputs):
    seeded = isinstance(row, FactorSeededSubmission)
    plan = session.get(
        FactorAnalysisPlan, row.analysis_plan_id if seeded else row.plan_id
    )
    if seeded:
        validate_saved_seed_binding(
            row,
            run,
            session.get(FactorResearchSpec, row.spec_id),
            plan,
            run.owner_id,
            run.idempotency_key,
        )
        request, _ = read_saved_seed(row)
    else:
        validate_saved_binding(row, run, plan, run.owner_id, run.idempotency_key)
        request = read_request(row)
    verify_steer_snapshot(session, run.owner_id, config.steer_snapshot)
    profile = resolve_provider_runtime(settings)
    _, binding = resolve_provider_declaration(
        settings, run.owner_id, request.model_profile_id
    )
    if (
        profile.runtime != config.runtime
        or binding != config.model_binding
        or profile.qlib_installed_parent_image_id != plan.qlib_installed_parent_image_id
    ):
        raise DomainError(
            "runtime_profile_changed", "Queued model/runtime declaration changed", 409
        )
    if read_inputs:
        checked = provider_preflight(
            session,
            run.owner_id,
            request,
            settings,
            accepted_steer_snapshot=config.steer_snapshot,
        )
        if not checked.can_submit or checked.preflight_sha256 != row.preflight_sha256:
            raise DomainError(
                "preflight_changed", "Queued immutable input differs", 409
            )
