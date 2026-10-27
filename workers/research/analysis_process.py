"""Persisted direct-analysis execution; default application catalog remains closed.

Only the explicitly maintained acceptance caller currently reaches the internal
issuer. It resolves actual retained graph/labels at every submission boundary;
operator image IDs and caller DTOs are not production acceptance evidence.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from backend.app.analysis_launch_services import (
    check_analysis_claim,
    claim_analysis_launch,
    record_analysis_containment,
    record_analysis_identity,
)
from backend.app.analysis_process_services import (
    persist_analysis_intent,
    record_analysis_stopped,
)
from backend.app.core.maintenance import shared_lifecycle
from backend.app.factor_native_io import native_storage_guard
from backend.app.research_broker_services import reserve_budget
from backend.app.research_lifecycle import neutral_transaction
from packages.qlib_adapter.direct_wire import write_input
from packages.research_runtime.analysis_process_journal import (
    PROFILE,
    AnalysisInputBinding,
    AnalysisIntent,
)
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.process_contracts import ProcessError, digest
from packages.research_runtime.process_declarations_v2 import (
    NoIPC,
    ProcessDeclarationV2,
)
from runtimes.research_supervisor.analysis_supervisor import (
    AnalysisSupervisor,
    _mint_analysis_authority,
)
from workers.research.numeric_process import _authorize_submission

ANALYSIS_LOCK_SHA256 = (
    "33cefc2eba90e996a848e0f8a92789654102364446d5bc290c94fb85f473a228"
)


def run_registered_analysis(*_args, **_kwargs):
    raise ProcessError("analysis_process_catalog_not_registered")


def execute_analysis_acceptance_unit(
    factory, fence, analysis_id, settings, *, authorize_submission=None
):
    """Actual retained-input test path, not generic API/dispatcher authority.

    Frozen plans must use the measured installed image and this fixed dependency
    lock. New default registration remains closed until composed actual gates.
    No retries, fabricated stop or reservation refunds on uncertain execution.
    """
    from backend.app.factor_analysis_services import (
        require_analysis_plan,
        resolve_direct_analysis_input,
    )

    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        native_storage_guard(settings.storage_root),
    ):
        with neutral_transaction(
            factory, fence, resource_scope="factor_numeric"
        ) as session:
            pins = require_analysis_plan(session, fence, analysis_id)
            _authorize_submission(session, fence, authorize_submission)
            resolve_direct_analysis_input(session, fence, analysis_id, settings)
            if pins.analysis_dependency_lock_sha256 != ANALYSIS_LOCK_SHA256:
                raise ProcessError("analysis_process_dependency_lock")
        supervisor = AnalysisSupervisor(
            pins.qlib_image_id, pins.qlib_installed_parent_image_id
        )
        inventory = supervisor.verified_inventory("analysis")
        with neutral_transaction(
            factory, fence, resource_scope="factor_numeric"
        ) as session:
            if require_analysis_plan(session, fence, analysis_id) != pins:
                raise ProcessError("analysis_process_plan_drift")
            value = resolve_direct_analysis_input(session, fence, analysis_id, settings)
            _authorize_submission(session, fence, authorize_submission)
            raw = write_input(value)
            binding = AnalysisInputBinding(
                analysis_id=analysis_id,
                snapshot_id=pins.snapshot_id,
                label_source_id=pins.label_source_id,
                protocol_sha256=value.protocol.sha256,
                analysis_input_sha256=hashlib.sha256(raw).hexdigest(),
                analysis_input_size_bytes=len(raw),
            )
            reservation = reserve_budget(
                session,
                fence,
                purpose="process_unit",
                settings=settings,
                logical_key="direct-analysis:" + str(analysis_id),
                amounts=BudgetVector(
                    process_slots=1,
                    evaluator_executions=1,
                    wall_milliseconds=60000,
                    storage_bytes=67_174_400,
                ),
            )
            admission = ProcessDeclarationV2(
                schema_version="mineagent.process-admission.v2",
                id=uuid4(),
                owner_id=fence.owner_id,
                run_id=fence.run_id,
                attempt_id=fence.attempt_id,
                generation=fence.generation,
                role="native_evaluator",
                logical_operation_id=analysis_id,
                launch_ordinal=1,
                profile=PROFILE,
                input_sha256=digest(binding.model_dump(mode="json")),
                reservation_id=reservation.id,
                image_id=pins.qlib_image_id,
                ipc=NoIPC(ipc_policy="none", ticket_sha256=None),
            )
            now = datetime.now(UTC)
            intent = AnalysisIntent(
                id=admission.id,
                admission=admission,
                inputs=binding,
                issued_at=now,
                not_after=now + timedelta(seconds=8),
            )
            persist_analysis_intent(session, fence, intent)
            claim_analysis_launch(session, fence, admission.id)
        authority = _mint_analysis_authority(
            intent, {"/input/analysis.json": raw}, inventory
        )

        def current():
            with neutral_transaction(
                factory, fence, resource_scope="factor_numeric"
            ) as session:
                check_analysis_claim(session, fence, intent)
                if (
                    require_analysis_plan(session, fence, analysis_id) != pins
                    or write_input(
                        resolve_direct_analysis_input(
                            session, fence, analysis_id, settings
                        )
                    )
                    != raw
                ):
                    raise ProcessError("analysis_process_retained_input_drift")
                _authorize_submission(session, fence, authorize_submission)

        def observed(_identity):
            with neutral_transaction(
                factory, fence, resource_scope="processes"
            ) as session:
                record_analysis_identity(
                    session, fence, admission.id, storage_root=settings.storage_root
                )

        try:
            current()
            supervisor.execute(
                settings.storage_root,
                authority,
                on_identity=observed,
                before_submission=current,
            )
            with neutral_transaction(
                factory, fence, resource_scope="processes"
            ) as session:
                record_analysis_stopped(
                    session,
                    fence,
                    admission.id,
                    storage_root=settings.storage_root,
                    with_output=True,
                )
        finally:
            with factory() as session:
                record_analysis_containment(
                    session, admission.id, storage_root=settings.storage_root
                )
                session.commit()
        return admission.id
