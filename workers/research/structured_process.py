"""Persisted closed structured-engine execution; default application catalog remains closed.

Only the explicitly maintained acceptance caller currently reaches the internal
issuer. It resolves actual retained snapshot/market graph at every submission boundary;
operator image IDs and caller DTOs are not production acceptance evidence.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from backend.app.core.maintenance import shared_lifecycle
from backend.app.factor_native_io import native_storage_guard
from backend.app.research_broker_services import reserve_budget
from backend.app.research_lifecycle import neutral_transaction
from backend.app.structured_launch_services import (
    check_structured_claim,
    claim_structured_launch,
    record_structured_containment,
    record_structured_identity,
)
from backend.app.structured_process_services import (
    persist_structured_intent,
    record_structured_stopped,
)
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.process_contracts import ProcessError, digest
from packages.research_runtime.process_declarations_v2 import (
    NoIPC,
    ProcessDeclarationV2,
)
from runtimes.research_supervisor.structured_factor_supervisor import (
    StructuredSupervisor,
    _mint_structured_authority,
)
from runtimes.structured_factor_engine.journal import (
    PROFILE,
    StructuredInputBinding,
    StructuredIntent,
)
from runtimes.structured_factor_engine.recipe import dependency_lock_sha256
from workers.research.numeric_process import _authorize_submission


def run_registered_structured(*_args, **_kwargs):
    raise ProcessError("structured_process_catalog_not_registered")


def execute_structured_acceptance_unit(
    factory, fence, execution_id, settings, *, authorize_submission=None
):
    """Actual retained-input test path, not generic API/dispatcher authority.

    Frozen plans must use the measured installed image and this fixed dependency
    lock. New default registration remains closed until composed actual gates.
    No retries, fabricated stop or reservation refunds on uncertain execution.
    """
    from backend.app.structured_factor_services import (
        require_structured_execution,
        resolve_structured_execution_input,
    )

    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        native_storage_guard(settings.storage_root),
    ):
        with neutral_transaction(
            factory, fence, resource_scope="factor_numeric"
        ) as session:
            pins = require_structured_execution(session, fence, execution_id)
            _authorize_submission(session, fence, authorize_submission)
            resolve_structured_execution_input(session, fence, execution_id, settings)
            if pins.engine_dependency_lock_sha256 != dependency_lock_sha256():
                raise ProcessError("structured_process_dependency_lock")
        supervisor = StructuredSupervisor(
            pins.engine_image_id, pins.engine_installed_parent_image_id
        )
        inventory = supervisor.verified_inventory("engine")
        with neutral_transaction(
            factory, fence, resource_scope="factor_numeric"
        ) as session:
            if require_structured_execution(session, fence, execution_id) != pins:
                raise ProcessError("structured_process_plan_drift")
            strategy, market = resolve_structured_execution_input(
                session, fence, execution_id, settings
            )
            _authorize_submission(session, fence, authorize_submission)
            binding = StructuredInputBinding(
                execution_id=execution_id,
                structured_strategy_version_id=pins.structured_strategy_version_id,
                snapshot_id=pins.snapshot_id,
                strategy_input_sha256=hashlib.sha256(strategy).hexdigest(),
                strategy_input_size_bytes=len(strategy),
                market_sha256=hashlib.sha256(market).hexdigest(),
                market_size_bytes=len(market),
            )
            reservation = reserve_budget(
                session,
                fence,
                purpose="process_unit",
                settings=settings,  # Requires the 024 physical installation-quota API.
                logical_key="structured-factor-execution:" + str(execution_id),
                amounts=BudgetVector(
                    process_slots=1,
                    evaluator_executions=1,
                    wall_milliseconds=90000,
                    storage_bytes=268_500_992,
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
                logical_operation_id=execution_id,
                launch_ordinal=1,
                profile=PROFILE,
                input_sha256=digest(binding.model_dump(mode="json")),
                reservation_id=reservation.id,
                image_id=pins.engine_image_id,
                ipc=NoIPC(ipc_policy="none", ticket_sha256=None),
            )
            now = datetime.now(UTC)
            intent = StructuredIntent(
                id=admission.id,
                admission=admission,
                inputs=binding,
                issued_at=now,
                not_after=now + timedelta(seconds=60),
            )
            persist_structured_intent(session, fence, intent)
            claim_structured_launch(session, fence, admission.id)
        authority = _mint_structured_authority(
            intent,
            {"/input/strategy.json": strategy, "/input/market.json": market},
            inventory,
        )

        def current():
            with neutral_transaction(
                factory, fence, resource_scope="factor_numeric"
            ) as session:
                check_structured_claim(session, fence, intent)
                if require_structured_execution(
                    session, fence, execution_id
                ) != pins or resolve_structured_execution_input(
                    session, fence, execution_id, settings
                ) != (strategy, market):
                    raise ProcessError("structured_process_retained_input_drift")
                _authorize_submission(session, fence, authorize_submission)

        def observed(_identity):
            with neutral_transaction(
                factory, fence, resource_scope="processes"
            ) as session:
                record_structured_identity(
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
                record_structured_stopped(
                    session,
                    fence,
                    admission.id,
                    storage_root=settings.storage_root,
                    with_output=True,
                )
        finally:
            with factory() as session:
                record_structured_containment(
                    session, admission.id, storage_root=settings.storage_root
                )
                session.commit()
        return admission.id
