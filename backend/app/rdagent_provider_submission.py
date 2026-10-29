"""026 normal authenticated-route writer for model-backed frozen declarations.

Flush-only, owner-serialized, no fake attempt and no provider call. Exact accepted
replay precedes current runtime/active-steer checks. This is not a fixture route.
"""

from uuid import uuid4

from sqlalchemy import text

from backend.app.factor_models import FactorVersion
from backend.app.models import OutboxEvent
from backend.app.rdagent_provider_preflight import provider_preflight
from backend.app.rdagent_provider_profile import (
    provider_seed_controller_profile,
    resolve_provider_runtime,
)
from backend.app.rdagent_provider_submission_contracts import (
    CATALOG,
    RDProviderSeedSubmit,
    RDProviderSubmit,
)
from backend.app.rdagent_seed_models import FactorSeededSubmission
from backend.app.rdagent_seed_services import resolve_seed_parent
from backend.app.rdagent_seed_submission import (
    existing_seeded_receipt,
    validate_saved_seed_binding,
)
from backend.app.rdagent_submission_models import FactorResearchSubmission
from backend.app.rdagent_submission_receipts import (
    existing_receipt,
    validate_key,
    validate_saved_binding,
)
from backend.app.rdagent_submission_services import _persist_queued_graph
from backend.app.research_lifecycle import _try_neutral_lock
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest


def submit_provider_run(session, owner_id, submitted, idempotency_key, settings):
    if type(submitted) not in (RDProviderSubmit, RDProviderSeedSubmit):
        raise DomainError(
            "request_drift", "Exact model-backed submission required", 422
        )
    validate_key(idempotency_key)
    if session.scalars(text("SELECT version_num FROM alembic_version")).all() not in (
        ["026_rd_seeded_submissions"],
        ["027_rd_controller_v3"],
        ["027a_contract_repairs"],
        ["027b_provider_material"],
        ["027c_seeded_controller_v4"],
        ["028_oe_structured_search"],
        ["029_research_workflow"],
        ["030_declared_upload_basket"],
        ["031_workflow_structured_stage"],
        ["032_normal_oe_note_pins"],
    ):
        raise DomainError(
            "engine_submission_schema_unavailable",
            "Configured model schema unavailable",
            409,
        )
    _try_neutral_lock(session, f"submit:{owner_id}")
    seeded = type(submitted) is RDProviderSeedSubmit
    previous = (existing_seeded_receipt if seeded else existing_receipt)(
        session, owner_id, idempotency_key, submitted
    )
    if previous is not None:
        return previous
    from backend.app.comparison_services import reject_comparison_key

    reject_comparison_key(session, owner_id, idempotency_key)
    request = submitted.request()
    checked = provider_preflight(session, owner_id, request, settings)
    if checked.preflight_sha256 != submitted.preflight_sha256:
        raise DomainError(
            "preflight_changed",
            "Selected model, guidance or retained source changed",
            409,
        )
    if not checked.can_submit:
        raise DomainError(
            "engine_request_ineligible",
            "Model-backed declaration is not currently eligible",
            409,
        )
    profile = resolve_provider_runtime(settings, seeded=seeded)
    if digest(profile) != checked.runtime_profile_sha256:
        raise DomainError("runtime_profile_changed", "Server runtime changed", 409)
    parent = (
        resolve_seed_parent(
            session, owner_id, request.parent_factor_version_id, settings
        )
        if seeded
        else None
    )
    if parent is not None and parent.reference != checked.parent:
        raise DomainError("seed_parent_drift", "Parent bytes changed", 409)
    run, spec, plan = _persist_queued_graph(
        session,
        owner_id,
        request,
        idempotency_key,
        settings,
        profile.runtime,
        profile.qlib_installed_parent_image_id,
        expected_input_sha256=checked.frozen_input_sha256,
        controller_selection=provider_seed_controller_profile(profile)
        if seeded
        else profile.controller_profile,
        model_binding=checked.model_binding,
        steer_snapshot=checked.steer_snapshot,
    )
    common = {
        "id": uuid4(),
        "owner_id": owner_id,
        "run_id": run.id,
        "idempotency_key": idempotency_key,
        "request_canonical": canonical_bytes(request).decode(),
        "request_sha256": request.sha256,
        "preflight_sha256": checked.preflight_sha256,
        "config_sha256": spec.config_sha256,
        "runtime_profile_sha256": digest(profile),
    }
    if parent is None:
        row = FactorResearchSubmission(
            **common, plan_id=plan.id, catalog_revision=CATALOG
        )
    else:
        ref = parent.reference
        version = session.get(FactorVersion, ref.factor_version_id)
        if (
            version is None
            or digest(version.source_provenance) != ref.source_provenance_sha256
        ):
            raise DomainError("seed_parent_drift", "Parent provenance changed", 409)
        row = FactorSeededSubmission(
            **common,
            spec_id=spec.id,
            analysis_plan_id=plan.id,
            parent_factor_id=ref.factor_id,
            parent_factor_version_id=ref.factor_version_id,
            parent_ordinal=ref.ordinal,
            parent_code_object_id=ref.code_object_id,
            parent_code_sha256=ref.code_sha256,
            parent_code_size_bytes=ref.code_size_bytes,
            parent_definition_sha256=ref.definition_sha256,
            parent_provenance_sha256=ref.source_provenance_sha256,
            parent_provenance_canonical=canonical_bytes(
                version.source_provenance
            ).decode(),
            parent_binding_sha256=ref.sha256,
        )
    session.add(row)
    session.add(OutboxEvent(id=uuid4(), run_id=run.id, generation=1))
    session.flush()
    receipt = (
        validate_saved_seed_binding(row, run, spec, plan, owner_id, idempotency_key)
        if seeded
        else validate_saved_binding(row, run, plan, owner_id, idempotency_key)
    )
    return receipt.model_copy(update={"replayed": False})
