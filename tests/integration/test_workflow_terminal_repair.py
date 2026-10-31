"""Actual PostgreSQL relational predicate, not a synthetic successful engine run."""

import pytest

pytestmark = pytest.mark.postgres


def test_terminal_repair_actual_predicate_and_reversible_maintenance(migrated_engine):
    from workflow_terminal_repair_fixture import statements

    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            for statement in statements():
                result = connection.exec_driver_sql(
                    statement, execution_options={"no_parameters": True}
                )
            assert result.scalar() == "TERMINAL032A_MATRIX_COMPLETE"
        finally:
            transaction.rollback()
