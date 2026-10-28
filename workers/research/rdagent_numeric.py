"""Configured retained-source numeric issuer; not registered in default dispatch.

Only revision022 and a current authenticated protected controller can call this
path. The previous maintained ABI fixture entrypoints remain unchanged.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select, text

from backend.app.core.maintenance import shared_lifecycle
from backend.app.numeric_process_services import persist_numeric_intent
from backend.app.rdagent_configured_inputs import (
    persist_configured_input_witness,
    resolve_candidate_inputs,
)
from backend.app.rdagent_controller_services import require_rd_controller
from backend.app.rdagent_models import FactorCutoffExecution
from backend.app.rdagent_services import _scope, require_research_spec
from backend.app.research_broker_services import reserve_budget
from backend.app.research_lifecycle import neutral_transaction
from packages.factors.contracts import digest
from packages.rdagent_adapter.numeric_profile import (
    CANDIDATE_LOCK_SHA256,
    NORMALIZER_LOCK_SHA256,
)
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.numeric_process_journal import NumericIntent
from packages.research_runtime.process_contracts import ProcessError
from packages.research_runtime.process_declarations_v2 import (
    NoIPC,
    ProcessDeclarationV2,
)
from runtimes.factor_execution.verify_inventory import (
    verify as verify_runtime_inventory,
)
from runtimes.research_supervisor.numeric_supervisor import NumericSupervisor
from workers.research.numeric_process import _execute_committed_numeric_unit


def require_configured_revision(session):
    # Exact revision, never >= comparison or arbitrary future-profile admission.
    if session.scalars(text("SELECT version_num FROM alembic_version")).all() not in (
        ["022_factor_analysis"],
        ["023_rd_submissions"],
        ["024_openevolve_initial"],
        ["025_structured_factor_strategy"],
        ["026_rd_seeded_submissions"],
        ["027_rd_controller_v3"],
        ["027a_contract_repairs"],
    ):
        raise ProcessError("configured_numeric_schema_closed")


def _authorize(session, fence, callback):
    if not callable(callback):
        raise ProcessError("configured_controller_required")
    peer = callback(session, fence)
    require_rd_controller(session, fence, peer)
    return peer


def _intent(session, fence, *, role, profile, image, logical_id, binding, settings):
    reservation = reserve_budget(
        session,
        fence,
        purpose="process_unit",
        settings=settings,
        logical_key="configured-numeric:" + uuid4().hex,
        amounts=BudgetVector(
            process_slots=1, wall_milliseconds=60000, storage_bytes=67_174_400
        ),
    )
    admission = ProcessDeclarationV2(
        schema_version="mineagent.process-admission.v2",
        id=uuid4(),
        **_scope(fence),
        role=role,
        logical_operation_id=logical_id,
        launch_ordinal=1,
        profile=profile,
        input_sha256=digest(binding.model_dump(mode="json")),
        reservation_id=reservation.id,
        image_id=image,
        ipc=NoIPC(ipc_policy="none", ticket_sha256=None),
    )
    now = datetime.now(UTC)
    intent = NumericIntent(
        schema_version="mineagent.numeric-process-intent.v2",
        id=uuid4(),
        admission=admission,
        inputs=binding,
        issued_at=now,
        not_after=now + timedelta(seconds=8),
    )
    persist_numeric_intent(session, fence, intent)
    return intent


def execute_configured_candidate(
    factory,
    fence,
    settings,
    *,
    evaluation_id,
    cutoff_ordinal,
    authorize_submission,
):
    """Persist full source/dataset witness atomically before exactly one launch.

    Existing cutoff obligations are never reexecuted by retry. Their owner must
    reconcile the original unit and read its retained output instead.
    """
    verify_runtime_inventory()
    with shared_lifecycle(factory.kw["bind"], settings.storage_root):
        with neutral_transaction(
            factory, fence, resource_scope="factor_numeric"
        ) as session:
            require_configured_revision(session)
            _authorize(session, fence, authorize_submission)
            _, config = require_research_spec(session, fence)
            locks = {item.role: item.sha256 for item in config.runtime.dependency_locks}
            if (
                locks["candidate"] != CANDIDATE_LOCK_SHA256
                or locks["normalizer"] != NORMALIZER_LOCK_SHA256
            ):
                raise ProcessError("configured_numeric_dependency")
            resolve_candidate_inputs(
                session,
                fence,
                evaluation_id=evaluation_id,
                cutoff_ordinal=cutoff_ordinal,
                settings=settings,
            )
        inventory = NumericSupervisor(
            config.runtime.candidate_image
        ).verified_inventory("candidate")
        NumericSupervisor(config.runtime.normalizer_image).verified_inventory(
            "normalizer"
        )
        with neutral_transaction(
            factory, fence, resource_scope="factor_numeric"
        ) as session:
            require_configured_revision(session)
            peer = _authorize(session, fence, authorize_submission)
            resolved = resolve_candidate_inputs(
                session,
                fence,
                evaluation_id=evaluation_id,
                cutoff_ordinal=cutoff_ordinal,
                settings=settings,
            )
            if (
                session.scalar(
                    select(FactorCutoffExecution).filter_by(
                        **_scope(fence),
                        evaluation_id=evaluation_id,
                        cutoff_ordinal=cutoff_ordinal,
                    )
                )
                is not None
            ):
                raise ProcessError("configured_cutoff_requires_reconciliation")
            cutoff_id = uuid4()
            intent = _intent(
                session,
                fence,
                role="factor_cutoff_candidate",
                profile=resolved.prefix.candidate_profile,
                image=resolved.prefix.candidate_image,
                logical_id=cutoff_id,
                binding=resolved.binding,
                settings=settings,
            )
            session.add(
                FactorCutoffExecution(
                    id=cutoff_id,
                    **_scope(fence),
                    evaluation_id=evaluation_id,
                    start_id=resolved.source.start_id,
                    factor_version_id=resolved.source.factor_version_id,
                    cutoff_ordinal=cutoff_ordinal,
                    projection=resolved.prefix.model_dump(mode="json"),
                    projection_sha256=resolved.prefix.sha256,
                    candidate_admission_id=intent.admission.id,
                    evaluated_source_id=resolved.source.source_id,
                )
            )
            session.flush()
            persist_configured_input_witness(
                session, fence, peer, execution_id=cutoff_id, settings=settings
            )
        _execute_committed_numeric_unit(
            factory,
            fence,
            settings.storage_root,
            intent.admission.id,
            input_bytes=dict(resolved.files),
            inventory=inventory,
            authorize_submission=authorize_submission,
        )
        return intent.admission.id


def execute_configured_normalizer(
    factory, fence, settings, *, publication_id, authorize_submission
):
    """Normalize exact stopped native output with required primitive report.

    Native bytes are opaque to this host. The current source/dataset input witness
    is reread before the one consumer claim and again on the later result reader.
    """
    import hashlib

    from backend.app.factor_native_io import native_storage_guard
    from backend.app.factor_native_services import (
        _publication,
        decode_intent,
        read_completed_native,
    )
    from backend.app.native_normalizer_claim import claim_native_normalizer
    from backend.app.numeric_launch_services import claim_numeric_launch
    from backend.app.rdagent_configured_inputs import require_configured_input_witness
    from packages.factors.contracts import canonical_bytes
    from packages.rdagent_adapter.numeric_profile import normalizer_inputs
    from packages.research_runtime.numeric_process_journal import (
        NumericInput,
        NumericInputFile,
    )
    from workers.research.numeric_process import _execute_claimed_numeric_unit

    verify_runtime_inventory()
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        native_storage_guard(settings.storage_root),
    ):
        with neutral_transaction(
            factory, fence, resource_scope="factor_numeric"
        ) as session:
            require_configured_revision(session)
            _authorize(session, fence, authorize_submission)
            publication = _publication(session, fence, publication_id)
            _, resolved = require_configured_input_witness(
                session, fence, execution_id=publication.cutoff_id, settings=settings
            )
            read_completed_native(session, fence, publication_id, settings)
        inventory = NumericSupervisor(
            resolved.prefix.normalizer_image
        ).verified_inventory("normalizer")
        with neutral_transaction(
            factory, fence, resource_scope="factor_numeric"
        ) as session:
            require_configured_revision(session)
            _authorize(session, fence, authorize_submission)
            publication = _publication(session, fence, publication_id)
            _, resolved = require_configured_input_witness(
                session, fence, execution_id=publication.cutoff_id, settings=settings
            )
            identity, raw = read_completed_native(
                session, fence, publication_id, settings
            )
            candidate = decode_intent(publication)
            if (candidate.source_object_id, candidate.source_sha256) != (
                resolved.source.code_object_id,
                resolved.source.code_sha256,
            ):
                raise ProcessError("configured_native_source_drift")
            prefix = resolved.prefix
            _, inputs, _ = normalizer_inputs(
                prefix,
                hdf=raw,
                expected_hdf_sha256=identity.object.sha256,
                primitive_report=True,
                listings=canonical_bytes(
                    [item.model_dump(mode="json") for item in prefix.listings]
                ),
            )
            binding = NumericInput(
                schema_version="mineagent.numeric-process-input.v2",
                source_object_id=resolved.source.code_object_id,
                source_sha256=resolved.source.code_sha256,
                prefix_sha256=prefix.sha256,
                cutoff=prefix.host_cutoff,
                files=tuple(
                    NumericInputFile(
                        destination=name,
                        sha256=hashlib.sha256(value).hexdigest(),
                        size_bytes=len(value),
                    )
                    for name, value in sorted(inputs.items())
                ),
            )
            intent = _intent(
                session,
                fence,
                role="factor_normalizer",
                profile=prefix.normalizer_profile,
                image=prefix.normalizer_image,
                logical_id=publication.cutoff_id,
                binding=binding,
                settings=settings,
            )
            claim_native_normalizer(
                session, fence, publication_id, intent.admission.id, settings
            )
            claimed = claim_numeric_launch(session, fence, intent.admission.id)
        _execute_claimed_numeric_unit(
            factory,
            fence,
            settings.storage_root,
            claimed,
            input_bytes=inputs,
            inventory=inventory,
            settings=settings,
            authorize_submission=authorize_submission,
        )
        return intent.admission.id
