"""Real PostgreSQL obligations and rejection paths, NOT actual numeric execution.

No synthetic stop/identity is inserted. Full runtime+RD-source execution remains
an independent required hosted gate before the closed launcher can be enabled.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from test_process_versioning import migrate
from test_research_process_recovery import (
    process_fence_fixture as shared_process_fence_fixture,
)

from backend.app.numeric_process_services import (
    persist_numeric_intent,
    read_numeric_output,
)
from backend.app.research_broker_services import initialize_budget, reserve_budget
from backend.app.research_lifecycle import neutral_transaction
from backend.app.research_process_models import (
    ResearchProcessAdmission,
    ResearchProcessReceipt,
)
from backend.app.research_process_services import as_admission, reconcile_attempt_units
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.numeric_process_journal import (
    NumericInput,
    NumericInputFile,
    NumericIntent,
)
from packages.research_runtime.process_contracts import ProcessError, digest
from packages.research_runtime.process_declarations_v2 import (
    NoIPC,
    ProcessDeclarationV2,
    require_v2_launch_authority,
)

process_fence_fixture = shared_process_fence_fixture
pytestmark = pytest.mark.postgres


@pytest.fixture
def numeric_obligation(migrated_engine, process_fence_fixture, settings):
    # This historical schema gate is revision017-specific even after later
    # composed source/native migrations become the default head. No RD rows
    # exist in this neutral obligation fixture, so guarded downgrade is genuine.
    migrate(migrated_engine, "017_numeric_process_boundary", downgrade=True)
    factory, fence = process_fence_fixture
    amount = BudgetVector(
        process_slots=1, wall_milliseconds=60000, storage_bytes=67_174_400
    )
    inputs = NumericInput(
        schema_version="mineagent.numeric-process-input.v2",
        source_object_id=uuid4(),
        source_sha256="a" * 64,
        prefix_sha256="a" * 64,
        cutoff=datetime(2020, 1, 1, tzinfo=UTC),
        files=tuple(
            NumericInputFile(destination=p, sha256="a" * 64, size_bytes=10)
            for p in (
                "/input/listings.json",
                "/input/manifest.json",
                "/input/observations.json",
                "/input/source.py",
            )
        ),
    )
    with neutral_transaction(factory, fence, resource_scope="processes") as session:
        initialize_budget(session, fence, amount)
        reservation = reserve_budget(
            session,
            fence,
            purpose="process_unit",
            settings=settings,
            logical_key="numeric-obligation",
            amounts=amount,
        )
        admission = ProcessDeclarationV2(
            schema_version="mineagent.process-admission.v2",
            id=uuid4(),
            owner_id=fence.owner_id,
            run_id=fence.run_id,
            attempt_id=fence.attempt_id,
            generation=fence.generation,
            role="factor_cutoff_candidate",
            logical_operation_id=uuid4(),
            launch_ordinal=1,
            profile="mineagent.factor-prefix-candidate.v2",
            input_sha256=digest(inputs.model_dump(mode="json")),
            reservation_id=reservation.id,
            image_id="sha256:" + "b" * 64,
            ipc=NoIPC(ipc_policy="none", ticket_sha256=None),
        )
        now = datetime.now(UTC)
        intent = NumericIntent(
            schema_version="mineagent.numeric-process-intent.v2",
            id=uuid4(),
            admission=admission,
            inputs=inputs,
            issued_at=now,
            not_after=now + timedelta(seconds=8),
        )
        persist_numeric_intent(session, fence, intent)
    return factory, fence, intent


def test_real_numeric_obligation_has_no_ticket_launch_stop_or_output(
    numeric_obligation, tmp_path
):
    factory, fence, intent = numeric_obligation
    with factory() as session:
        row = session.get(ResearchProcessAdmission, intent.admission.id)
        assert (
            row.role == "factor_cutoff_candidate"
            and row.ticket_sha256 is None
            and row.ipc_policy == "none"
        )
        receipts = session.scalars(
            select(ResearchProcessReceipt).where(
                ResearchProcessReceipt.admission_id == row.id
            )
        ).all()
        assert [r.kind for r in receipts] == ["launch_intent"]
        assert not reconcile_attempt_units(
            session, fence.run_id, fence.attempt_id, fence.generation
        ).confirmed
        with pytest.raises(ProcessError):
            as_admission(row)
    with pytest.raises(ProcessError, match="not_activated"):
        require_v2_launch_authority(intent.admission)
    with (
        neutral_transaction(factory, fence, resource_scope="processes") as session,
        pytest.raises(ProcessError, match="uncommitted"),
    ):
        read_numeric_output(session, fence, intent.admission.id, storage_root=tmp_path)
    with (
        neutral_transaction(factory, fence, resource_scope="processes") as session,
        pytest.raises(ProcessError, match="already_admitted"),
    ):
        persist_numeric_intent(session, fence, intent)


def test_real_numeric_evidence_prevents_downgrade(migrated_engine, numeric_obligation):
    with pytest.raises(DBAPIError, match="Numeric process evidence prevents downgrade"):
        migrate(migrated_engine, "016_process_versioning", downgrade=True)
    with migrated_engine.connect() as conn:
        assert (
            conn.scalar(text("SELECT version_num FROM alembic_version"))
            == "017_numeric_process_boundary"
        )


@pytest.mark.parametrize(
    "field,value",
    [
        (
            "ticket_sha256",
            "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        ),
        ("ipc_policy", "host_ticket"),
        ("profile", "unknown.v2"),
        ("role", "rd_orchestrator"),
    ],
    ids=["ticket", "policy", "profile", "role"],
)
def test_real_numeric_immutable_rows_reject_mutation(
    migrated_engine, numeric_obligation, field, value
):
    with (
        migrated_engine.begin() as conn,
        pytest.raises(DBAPIError),
        conn.begin_nested(),
    ):
        conn.execute(
            text(f"UPDATE research_process_admissions SET {field}=:value"),
            {"value": value},
        )
