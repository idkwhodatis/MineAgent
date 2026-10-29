"""Normal027 terminal write only after fresh stopped epoch and exact obligations.

No arbitrary exception/stop flag becomes success. No fabricated aggregate exit
code, score or result release is published by this control-plane transition.
"""

from sqlalchemy import select

from backend.app.core.maintenance import shared_lifecycle
from backend.app.factor_native_io import native_storage_guard
from backend.app.oe_structured_completion_services import complete_stopped_epoch
from backend.app.oe_structured_identity import decode_spec, fail
from backend.app.oe_structured_models import OEStructuredEngineOutcome
from backend.app.oe_structured_operational_models import OEStructuredEpoch
from backend.app.research_broker_models import ResearchBudgetControl
from backend.app.research_lifecycle import (
    finish_oe_structured_transaction,
    neutral_transaction,
    require_neutral_transaction,
)
from backend.app.research_process_models import (
    ResearchProcessAdmission,
    ResearchProcessState,
)
from packages.research_runtime.budget_contracts import BudgetVector


def _vector(value):
    if not isinstance(value, dict) or set(value) != set(BudgetVector.model_fields):
        fail()
    return BudgetVector.model_validate(value)


def finish_structured_run(factory, fence, epoch_id, settings):
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        native_storage_guard(settings.storage_root),
        neutral_transaction(factory, fence, resource_scope="factor_numeric") as session,
    ):
        return finish_oe_structured_transaction(session, fence, epoch_id, settings)


def _validate_current_success(session, fence, epoch_id, settings):
    run, _ = require_neutral_transaction(
        session, fence, resource_scope="factor_numeric"
    )
    _, config = decode_spec(session, run)
    final = complete_stopped_epoch(session, fence, epoch_id, settings)
    epoch = session.get(OEStructuredEpoch, epoch_id)
    scope = {
        key: getattr(fence, key)
        for key in ("owner_id", "run_id", "attempt_id", "generation")
    }
    outcomes = list(
        session.scalars(select(OEStructuredEngineOutcome).filter_by(**scope).limit(4))
    )
    admissions = list(
        session.scalars(select(ResearchProcessAdmission).filter_by(**scope).limit(5))
    )
    states = list(
        session.scalars(select(ResearchProcessState).filter_by(**scope).limit(5))
    )
    if (
        epoch is None
        or final.epoch_id != epoch_id
        or any(getattr(epoch, k) != v for k, v in scope.items())
        or not 1 <= len(outcomes) <= 1 + config.search.maximum_mutation_slots
        or len(admissions) != 1 + len(outcomes)
        or {row.id for row in admissions}
        != {epoch.controller_admission_id} | {row.admission_id for row in outcomes}
        or len(states) != len(admissions)
        or {row.admission_id for row in states} != {row.id for row in admissions}
        or any(row.status != "stopped" for row in states)
    ):
        fail()
    budget = session.scalar(
        select(ResearchBudgetControl)
        .filter_by(owner_id=fence.owner_id, run_id=fence.run_id)
        .with_for_update()
    )
    if (
        budget is None
        or _vector(budget.limits) != config.budget
        or _vector(budget.reserved) != BudgetVector()
        or not config.budget.covers(_vector(budget.used))
    ):
        fail()
    require_neutral_transaction(session, fence, resource_scope="factor_numeric")
    return final.id
