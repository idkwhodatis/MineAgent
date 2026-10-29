"""One-shot027 trusted-engine issuer from fresh persisted candidate provenance.

Reuses the fixed measured engine supervisor, not025's acceptance initializer.
All input/image/dependency choices come from current owned027 declarations.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from backend.app.core.maintenance import shared_lifecycle
from backend.app.factor_native_io import native_storage_guard
from backend.app.oe_structured_engine_services import (
    execution_input,
    fail,
    require_execution,
)
from backend.app.research_broker_services import reserve_budget
from backend.app.research_lifecycle import neutral_transaction
from backend.app.research_process_models import ResearchProcessAdmission
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
from packages.research_runtime.process_contracts import digest
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

PROCESS_AMOUNT = BudgetVector(
    process_slots=1,
    evaluator_executions=1,
    wall_milliseconds=90000,
    storage_bytes=268500992,
)


def _no_prior_process(session, execution_id):
    if (
        session.scalar(
            select(ResearchProcessAdmission.id)
            .where(ResearchProcessAdmission.logical_operation_id == execution_id)
            .limit(1)
        )
        is not None
    ):
        fail("oe_structured_engine_already_admitted")


def execute_candidate_engine(factory, fence, execution_id, settings, *, peer):
    """Never retries an uncertain or completed native unit. Read outcomes separately."""
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        native_storage_guard(settings.storage_root),
    ):
        with neutral_transaction(
            factory, fence, resource_scope="factor_numeric"
        ) as session:
            pins = require_execution(session, fence, execution_id, settings, peer=peer)
            execution_input(session, fence, execution_id, settings, peer=peer)
            _no_prior_process(session, execution_id)
            if pins.engine_dependency_lock_sha256 != dependency_lock_sha256():
                fail("oe_structured_engine_dependency_lock")
        supervisor = StructuredSupervisor(
            pins.engine_image_id, pins.engine_installed_parent_image_id
        )
        inventory = supervisor.verified_inventory("engine")
        with neutral_transaction(
            factory, fence, resource_scope="factor_numeric"
        ) as session:
            if (
                require_execution(session, fence, execution_id, settings, peer=peer)
                != pins
            ):
                fail("oe_structured_engine_plan_drift")
            strategy, market = execution_input(
                session, fence, execution_id, settings, peer=peer
            )
            _no_prior_process(session, execution_id)
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
                settings=settings,
                logical_key="structured-factor-execution:" + str(execution_id),
                amounts=PROCESS_AMOUNT,
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
                if require_execution(
                    session, fence, execution_id, settings, peer=peer
                ) != pins or execution_input(
                    session, fence, execution_id, settings, peer=peer
                ) != (strategy, market):
                    fail("oe_structured_engine_retained_input_drift")

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
            # Missing proof stays an error and held reservation, never a fake score.
            with factory() as session:
                record_structured_containment(
                    session, admission.id, storage_root=settings.storage_root
                )
                session.commit()
        return admission.id
