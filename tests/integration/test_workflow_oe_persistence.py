"""Installed031 metadata and exact grammar, not actual engine-flow acceptance."""

import pytest

pytestmark = pytest.mark.postgres


def test_explicit_workflow_oe_contracts_and_final_prompt(migrated_engine):
    from workflow_oe_sql_fixture import statements

    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            for statement in statements():
                result = connection.exec_driver_sql(statement)
            assert result.scalar() == "OE031_CONTRACT_MATRIX_COMPLETE"
        finally:
            transaction.rollback()


def test_new_finite_plan_retains_original_inputs_and_requires_real_children(
    migrated_engine,
):
    from workflow_oe_sql_fixture import declaration_statements

    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            for statement in declaration_statements():
                result = connection.exec_driver_sql(statement)
            assert result.scalar() == "OE031_DECLARATION_COMPLETE"
        finally:
            transaction.rollback()


def test_old_rd_only_root_rejects_orphan_oe_claim(migrated_engine):
    from workflow_oe_sql_fixture import orphan_claim_statements

    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            for statement in orphan_claim_statements():
                result = connection.exec_driver_sql(statement)
            assert result.scalar() == "OE031_ORPHAN_CLAIM_REJECTED"
        finally:
            transaction.rollback()
