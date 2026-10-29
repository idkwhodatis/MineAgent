"""Normal027 queued submission; actual worker alone later creates an attempt.

Owns lifecycle/owner/retained-byte transaction through commit. Runtime and schema
are closed by default. A preflight hash is compared with a fresh read, never
accepted as caller-provided execution authority. Exact accepted replays create
no second outbox event and do not require a currently enabled provider.
"""

from uuid import uuid4

from sqlalchemy import func, null, select, text

from backend.app.core.maintenance import shared_lifecycle
from backend.app.experiment_services import _identities
from backend.app.models import (
    DatasetVersion,
    DataUsageExposure,
    OutboxEvent,
    ResearchRun,
)
from backend.app.oe_structured_models import OEStructuredSpec, OEStructuredSubmission
from backend.app.oe_structured_preflight import REVISION, evaluate
from backend.app.oe_structured_provider_input import retained_submission_data
from backend.app.oe_structured_provider_submission import (
    ProviderStructuredSearchSubmit,
    ProviderStructuredSubmissionReceipt,
)
from backend.app.oe_structured_submission_receipts import existing_receipt, validate_key
from backend.app.research_broker_models import ResearchBudgetControl
from backend.app.research_lifecycle import _try_neutral_lock
from backend.app.research_services import TERMINAL, quota_limit
from backend.app.services import DomainError
from backend.app.structured_factor_models import StructuredStrategyVersion
from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured.candidate import (
    CandidateDocument,
    TypedFrozenCandidateDomain,
    _raw_fields,
)
from packages.openevolve_structured.contracts import StructuredSearchSpec
from packages.openevolve_structured.provider_run import ProviderStructuredRunConfig
from packages.research_runtime.budget_contracts import BudgetVector


def submit(factory, owner_id, submitted, key, settings):
    if type(submitted) is not ProviderStructuredSearchSubmit:
        raise DomainError(
            "invalid_request", "Exact structured provider submission required", 422
        )
    submitted = ProviderStructuredSearchSubmit.model_validate(_raw_fields(submitted))
    validate_key(key)
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        factory() as session,
    ):
        if session.scalars(
            text("SELECT version_num FROM alembic_version")
        ).all() not in (
            [REVISION],
            ["029_research_workflow"],
            ["030_declared_upload_basket"],
            ["031_workflow_structured_stage"],
            ["032_normal_oe_note_pins"],
        ):
            raise DomainError(
                "runtime_schema_unavailable",
                "Structured search schema is unavailable",
                409,
            )
        _try_neutral_lock(session, f"submit:{owner_id}")
        previous = existing_receipt(session, owner_id, key, submitted)
        if previous is not None:
            session.commit()
            return previous
        from backend.app.comparison_services import reject_comparison_key

        reject_comparison_key(session, owner_id, key)
        request = submitted.request()
        with retained_submission_data(session, owner_id, request, settings) as data:
            checked, fixed, runtime, binding = evaluate(
                session, owner_id, request, settings, data
            )
            if (
                not checked.can_submit
                or checked.preflight_sha256 != submitted.preflight_sha256
            ):
                raise DomainError(
                    "preflight_changed",
                    "Current data, model, runtime or budget eligibility differs",
                    409,
                )
            active = session.scalar(
                select(func.count())
                .select_from(ResearchRun)
                .where(
                    ResearchRun.owner_id == owner_id,
                    ResearchRun.status.not_in(TERMINAL),
                )
            )
            if active >= quota_limit(session, owner_id, settings):
                raise DomainError("run_quota", "Active run limit reached", 429)
            receipt = _persist_queued(
                session, owner_id, key, request, checked, fixed, runtime, binding, data
            )
            # Keep FD guard and owner lock until the declaration/outbox is durable.
            # A later cleanup exception is an uncertain ACK; key replay reads it.
            session.commit()
            return receipt


def _persist_queued(
    session, owner_id, key, request, checked, fixed, runtime, binding, data
):
    run_id, version_id, submission_id = uuid4(), uuid4(), uuid4()
    program = request.seed_parameters.program(data.manifest.input.window)
    search = StructuredSearchSpec(
        owner_id=owner_id,
        run_id=run_id,
        execution_binding_id=run_id,
        execution_binding_sha256=digest(fixed),
        domain=TypedFrozenCandidateDomain(seed_program=program),
        seed_candidate_sha256=digest(CandidateDocument(program=program)),
        objective=request.objective,
        maximum_mutation_slots=request.maximum_mutation_slots,
    )
    config = ProviderStructuredRunConfig(
        search=search,
        fixed_inputs=fixed,
        model_binding=binding,
        runtime=runtime.runtime,
        budget=request.budget,
        authenticated_request_sha256=request.sha256,
    )
    version = StructuredStrategyVersion(
        id=version_id,
        owner_id=owner_id,
        parent_version_id=None,
        program=program.model_dump(mode="json"),
        program_canonical=canonical_bytes(program).decode("ascii"),
        program_sha256=digest(program),
    )
    run = ResearchRun(
        id=run_id,
        owner_id=owner_id,
        idempotency_key=key,
        request_sha256=digest(config),
        config=config.model_dump(mode="json"),
        purpose="strategy_search",
        engine="openevolve",
        dataset_version_id=fixed.dataset_version_id,
        strategy_version_id=None,
        submission_binding=null(),
        status="queued",
        generation=1,
        active_attempt_id=None,
        research_image_digest=runtime.runtime.controller_image_id,
    )
    session.add_all((version, run))
    session.flush()
    submission = OEStructuredSubmission(
        id=submission_id,
        owner_id=owner_id,
        run_id=run_id,
        idempotency_key=key,
        request_canonical=canonical_bytes(request).decode("ascii"),
        request_sha256=request.sha256,
        preflight_sha256=checked.preflight_sha256,
        config_sha256=digest(config),
        origin="authenticated_user_submission",
    )
    session.add(submission)
    session.flush()
    session.add(
        OEStructuredSpec(
            run_id=run_id,
            owner_id=owner_id,
            submission_id=submission_id,
            request=request.model_dump(mode="json"),
            request_canonical=submission.request_canonical,
            request_sha256=request.sha256,
            preflight_sha256=checked.preflight_sha256,
            config=config.model_dump(mode="json"),
            config_canonical=canonical_bytes(config).decode("ascii"),
            config_sha256=digest(config),
            snapshot_id=fixed.snapshot_id,
            dataset_id=fixed.dataset_version_id,
            seed_program_version_id=version_id,
        )
    )
    dataset = session.get(DatasetVersion, fixed.dataset_version_id)
    if (
        dataset is None
        or dataset.owner_id != owner_id
        or dataset.content_sha256 != fixed.dataset_sha256
    ):
        raise DomainError("preflight_changed", "Owned retained dataset differs", 409)
    session.add(
        DataUsageExposure(
            id=uuid4(),
            owner_id=owner_id,
            dataset_version_id=dataset.id,
            run_id=run_id,
            experiment_id=None,
            split_version_id=None,
            source_key="oe-structured-source:" + str(run_id),
            identities=_identities(session, dataset),
            start_date=dataset.start_date,
            end_date=dataset.end_date,
            purpose="oe_structured_execution_source",
            provenance={
                "schema_version": "mineagent.oe-structured-source-exposure.v1",
                "config_sha256": digest(config),
                "fixed_input_sha256": digest(fixed),
                "snapshot_id": str(fixed.snapshot_id),
                "dataset_manifest_sha256": fixed.dataset_sha256,
                "extent": "whole_retained_dataset",
                "qualification": (
                    "user_declared_fixed_snapshot_not_verified_pit_or_causal_gain"
                    if fixed.market_qualification == "user_declaration_unverified"
                    else "synthetic_fixed_snapshot_not_verified_pit_or_causal_gain"
                ),
            },
        )
    )
    session.add(
        ResearchBudgetControl(
            id=uuid4(),
            owner_id=owner_id,
            run_id=run_id,
            limits=config.budget.model_dump(),
            used=BudgetVector().model_dump(),
            reserved=BudgetVector().model_dump(),
        )
    )
    session.add(OutboxEvent(id=uuid4(), run_id=run_id, generation=1))
    session.flush()
    return ProviderStructuredSubmissionReceipt(
        id=submission_id,
        run_id=run_id,
        origin_user_id=owner_id,
        request_sha256=request.sha256,
        preflight_sha256=checked.preflight_sha256,
        normalized_request=request,
        required_process_units=request.required_process_units,
    )
