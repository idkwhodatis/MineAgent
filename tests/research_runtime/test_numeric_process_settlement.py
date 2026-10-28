"""Real journal/ledger/ORM rollback; SQLite omits PG triggers and kernel proof.

Only current-fence authority is doubled. Synthetic protected records from the
existing fixture are not actual container execution or a PostgreSQL gate.
"""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from backend.app import research_lifecycle
from backend.app.numeric_process_services import record_numeric_stopped
from backend.app.research_broker_models import (
    ResearchBudgetControl,
    ResearchBudgetReservation,
    ResearchBudgetSettlement,
)
from backend.app.research_process_models import (
    ResearchProcessAdmission,
    ResearchProcessReceipt,
    ResearchProcessState,
)
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.process_contracts import ProcessError, digest
from packages.research_runtime.process_persistence_codec import encode_row
from tests.research_runtime.test_numeric_process_boundary import journal


@pytest.fixture(params=["factor_cutoff_candidate", "factor_normalizer"])
def case(tmp_path, monkeypatch, request):
    admission, launch, _identity, _stop, _extraction, _payload, path = journal(
        tmp_path, request.param
    )
    fence = NS(
        **{
            k: getattr(admission, k)
            for k in ("owner_id", "run_id", "attempt_id", "generation")
        }
    )
    engine = create_engine("sqlite://")
    models = (
        ResearchBudgetControl,
        ResearchBudgetReservation,
        ResearchBudgetSettlement,
        ResearchProcessAdmission,
        ResearchProcessReceipt,
        ResearchProcessState,
    )
    with engine.begin() as connection:
        for model in models:
            columns = []
            for c in model.__table__.columns:
                kind = (
                    "INTEGER" if c.name in {"generation", "launch_ordinal"} else "TEXT"
                )
                columns.append(
                    c.name + " " + kind + (" PRIMARY KEY" if c.primary_key else "")
                )
            connection.execute(
                text(
                    "CREATE TABLE "
                    + model.__tablename__
                    + " ("
                    + ",".join(columns)
                    + ")"
                )
            )
    amount = BudgetVector(
        process_slots=1, wall_milliseconds=60000, storage_bytes=67174400
    )
    with Session(engine) as session, session.begin():
        session.add(
            ResearchBudgetControl(
                id=uuid4(),
                owner_id=fence.owner_id,
                run_id=fence.run_id,
                limits=amount.model_dump(),
                used=BudgetVector().model_dump(),
                reserved=amount.model_dump(),
            )
        )
        session.add(
            ResearchBudgetReservation(
                id=admission.reservation_id,
                **vars(fence),
                purpose="process_unit",
                logical_key="actual-unit-fixture",
                amounts=amount.model_dump(),
                state="reserved",
            )
        )
        session.add(ResearchProcessAdmission(**encode_row(admission)))
        session.add(
            ResearchProcessState(
                admission_id=admission.id, **vars(fence), status="running"
            )
        )
        session.add(
            ResearchProcessReceipt(
                id=uuid4(),
                admission_id=admission.id,
                **vars(fence),
                kind="launch_intent",
                payload=launch.intent.model_dump(mode="json"),
                payload_sha256=digest(launch.intent.model_dump(mode="json")),
            )
        )

    def authorize(session, actual, **kwargs):
        assert session.in_transaction() and actual is fence

    monkeypatch.setattr(research_lifecycle, "require_neutral_transaction", authorize)
    yield NS(
        engine=engine,
        root=tmp_path,
        path=path,
        admission=admission,
        fence=fence,
        amount=amount,
    )
    engine.dispose()


def record(session, c, **kwargs):
    return record_numeric_stopped(
        session,
        c.fence,
        c.admission.id,
        storage_root=c.root,
        with_output=kwargs.get("with_output", True),
    )


def assert_reserved(c):
    with Session(c.engine) as session:
        assert (
            session.get(ResearchBudgetReservation, c.admission.reservation_id).state
            == "reserved"
        )
        assert session.scalar(select(ResearchBudgetSettlement)) is None
        assert session.get(ResearchProcessState, c.admission.id).status == "running"


def test_success_full_conservative_charge_and_exact_replay(case):
    c = case
    with Session(c.engine) as session, session.begin():
        record(session, c)
        settlement = session.scalar(select(ResearchBudgetSettlement))
        assert settlement.outcome == "conservative_unknown"
        assert settlement.charge == c.amount.model_dump()
        from backend.app.execution_snapshot_history import process_history

        # Exercise the unchanged strict reader that rejected the hosted run.
        saved, receipts = process_history(session, c.fence, c.admission.id)
        assert saved.id == c.admission.id
        assert set(receipts) == {
            "launch_intent",
            "launch_identity",
            "stop",
            "extraction",
        }
        identity = settlement.id
        record(session, c)
        assert session.scalar(select(ResearchBudgetSettlement)).id == identity
        ledger = session.scalar(select(ResearchBudgetControl))
        assert ledger.reserved == BudgetVector().model_dump()
        assert ledger.used == c.amount.model_dump()
    with Session(c.engine) as session, session.begin():
        record(session, c)
        assert session.scalar(select(ResearchBudgetSettlement)).id == identity


def test_outer_abort_rolls_back_stop_receipts_and_settlement(case):
    with pytest.raises(RuntimeError), Session(case.engine) as session, session.begin():
        record(session, case)
        raise RuntimeError("commit not reached")
    assert_reserved(case)


@pytest.mark.parametrize(
    "failure", ["missing_stop", "changed_output", "missing_extraction", "fence"]
)
def test_missing_fresh_proof_never_settles(case, monkeypatch, failure):
    if failure == "fence":

        def reject(*args, **kwargs):
            raise ProcessError("stale_fence")

        monkeypatch.setattr(research_lifecycle, "require_neutral_transaction", reject)
    elif failure == "changed_output":
        next((case.path / "output").iterdir()).write_bytes(b"tampered")
    else:
        name = "stop" if failure == "missing_stop" else "extraction"
        (case.path / "control" / (name + ".json")).unlink()
    with (
        pytest.raises((ProcessError, OSError, ValueError)),
        Session(case.engine) as session,
        session.begin(),
    ):
        record(session, case)
    assert_reserved(case)


def test_stop_only_does_not_gain_settlement_authority(case):
    with Session(case.engine) as session, session.begin():
        record(session, case, with_output=False)
        assert (
            session.get(ResearchBudgetReservation, case.admission.reservation_id).state
            == "reserved"
        )
        assert session.scalar(select(ResearchBudgetSettlement)) is None


@pytest.mark.parametrize("field", ["charge", "outcome"])
def test_existing_settlement_must_keep_full_charge_and_outcome(case, field):
    from packages.research_runtime.control import BrokerAbort

    with Session(case.engine) as session, session.begin():
        record(session, case)
        settled = session.scalar(select(ResearchBudgetSettlement))
        setattr(
            settled,
            field,
            BudgetVector().model_dump() if field == "charge" else "measured",
        )
        session.flush()
        with pytest.raises(BrokerAbort):
            record(session, case)


@pytest.mark.parametrize("field", ["owner_id", "attempt_id", "purpose"])
def test_wrong_reservation_scope_or_purpose_rolls_back_stop(case, field):
    with Session(case.engine) as session, session.begin():
        row = session.get(ResearchBudgetReservation, case.admission.reservation_id)
        setattr(row, field, "object_storage" if field == "purpose" else uuid4())
    with pytest.raises(ProcessError), Session(case.engine) as session, session.begin():
        record(session, case)
    assert_reserved(case)


def test_foreign_process_scope_rejected_before_journal_and_charge(case):
    case.fence.owner_id = uuid4()
    with pytest.raises(ProcessError), Session(case.engine) as session, session.begin():
        record(session, case)
    assert_reserved(case)
