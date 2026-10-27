"""Reusable actual ten-unit structured fixture; no mocked execution authority.

Two complete configured factor runs plus two protected engine runs. Reused by
independent live/relocation and real database cold-restore acceptance cases.
"""

from datetime import date

import pytest


def assert_engine_oracle(result, *, listing_id, quantity, closing_price):
    """Independent fixed-fixture arithmetic, not expected values read from output."""
    assert result.result_trust == "closed_operator_trusted_engine"
    assert len(result.decisions) == 2
    signal, hold = result.decisions
    assert signal.observation.session_date == date(2024, 3, 13)
    assert len(signal.reads) == 3
    assert len(signal.targets) == 1
    assert str(signal.targets[0].listing_id) == listing_id
    assert signal.targets[0].value == 0.5
    assert hold.observation.session_date == date(2024, 3, 14)
    assert hold.reads == () and hold.targets == ()
    tables = result.accounting_tables
    assert len(tables["orders"]) == len(tables["fills"]) == 1
    order, fill = tables["orders"][0], tables["fills"][0]
    assert order["listing_id"] == fill["listing_id"] == listing_id
    assert order["side"] == fill["side"] == "buy"
    assert order["quantity"] == order["filled_quantity"] == fill["quantity"] == quantity
    assert order["status"] == "filled"
    assert fill["order_id"] == order["order_id"]
    assert fill["logical_order_id"] == order["logical_order_id"]
    assert fill["session_date"] == "2024-03-14"
    assert fill["decision_session"] == "2024-03-13"
    # Literal fixture open and fixed reviewed native cost profile, independent of
    # result.engine/resolved_config and of reported summary/financial metrics.
    price = 100.0 * 1.0005
    commission = quantity * price * 0.0005
    cash = 10000.0 - quantity * price - commission
    equity = cash + quantity * closing_price
    assert fill["price"] == pytest.approx(price, abs=1e-8, rel=0)
    assert fill["commission"] == pytest.approx(commission, abs=1e-8, rel=0)
    assert order["commission"] == pytest.approx(commission, abs=1e-8, rel=0)
    assert tables["account_series"][-1]["cash"] == pytest.approx(cash, abs=1e-8, rel=0)
    assert tables["account_series"][-1]["equity"] == pytest.approx(
        equity, abs=1e-8, rel=0
    )
    assert tables["summary"]["final_equity"] == pytest.approx(equity, abs=1e-8, rel=0)
    assert tables["summary"]["execution_count"] == 1


def run_structured_pair(engine, settings, owner_id):
    from sqlalchemy import select
    from structured_factor_fixture import prepare_structured_pair

    from backend.app.core.maintenance import shared_lifecycle
    from backend.app.factor_native_io import native_storage_guard
    from backend.app.models import DatasetVersion, ResearchRun
    from backend.app.research_lifecycle import neutral_transaction
    from backend.app.research_process_models import (
        ResearchProcessAdmission,
        ResearchProcessState,
    )
    from backend.app.structured_factor_results import (
        persist_structured_execution_outcome,
        read_verified_structured_execution,
    )
    from workers.research.structured_process import execute_structured_acceptance_unit

    factory, fence, execution_ids = prepare_structured_pair(engine, settings, owner_id)
    with factory() as session:
        run = session.get(ResearchRun, fence.run_id)
        dataset = session.get(DatasetVersion, run.dataset_version_id)
        listing_ids = {
            x["symbol"]: x["listing_id"] for x in dataset.manifest["listings"]
        }
    outputs = []
    for execution_id, symbol, quantity, close in zip(
        execution_ids, ("AAA", "BBB"), (48, 49), (110.0, 104.0), strict=True
    ):
        admission_id = execute_structured_acceptance_unit(
            factory, fence, execution_id, settings
        )
        with (
            shared_lifecycle(factory.kw["bind"], settings.storage_root),
            native_storage_guard(settings.storage_root),
        ):
            with neutral_transaction(
                factory, fence, resource_scope="factor_numeric"
            ) as session:
                persist_structured_execution_outcome(
                    session, fence, execution_id, admission_id, settings
                )
            with neutral_transaction(
                factory, fence, resource_scope="factor_numeric"
            ) as session:
                verified = read_verified_structured_execution(
                    session, fence, execution_id, settings
                )
                assert verified.admission_id == admission_id
                assert_engine_oracle(
                    verified.result,
                    listing_id=listing_ids[symbol],
                    quantity=quantity,
                    closing_price=close,
                )
                outputs.append(verified)
            # Completed reread must not create another unit or re-open a hold.
            with neutral_transaction(
                factory, fence, resource_scope="factor_numeric"
            ) as session:
                assert (
                    read_verified_structured_execution(
                        session, fence, execution_id, settings
                    )
                    == verified
                )
    assert outputs[0].snapshot_id != outputs[1].snapshot_id
    assert outputs[0].result_sha256 != outputs[1].result_sha256
    assert (
        outputs[0].result.structured_strategy_version_id
        == outputs[1].result.structured_strategy_version_id
    )
    with factory() as session:
        rows = session.scalars(
            select(ResearchProcessAdmission).filter_by(run_id=fence.run_id)
        ).all()
        assert len(rows) == 2
        assert {r.logical_operation_id for r in rows} == set(execution_ids)
        assert all(r.profile == "mineagent.factor-strategy-engine.v1" for r in rows)
        assert all(
            session.get(ResearchProcessState, r.id).status == "stopped" for r in rows
        )
    from backend.app.structured_factor_history import (
        read_historical_structured_execution,
    )
    from workers.research.structured_factor import finish_structured_acceptance_run

    finish_structured_acceptance_run(factory, fence, settings)
    for expected in outputs:
        historical = read_historical_structured_execution(
            factory, owner_id, expected.execution_id, settings
        )
        assert historical.result == expected.result
        assert historical.result_sha256 == expected.result_sha256

    return factory, fence, execution_ids, tuple(outputs)
