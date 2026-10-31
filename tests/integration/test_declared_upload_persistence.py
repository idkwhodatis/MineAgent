"""Installed030 real constraints; metadata fixtures do not attest uploaded bytes."""

import pytest
from declared_upload_sql_fixture import probe_statements

pytestmark = pytest.mark.postgres


def test_declared_upload_owner_lineage_and_qualification_constraints(migrated_engine):
    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            for statement in probe_statements():
                result = connection.exec_driver_sql(
                    statement, execution_options={"no_parameters": True}
                )
            assert result.scalar() == "DECLARED_UPLOAD_030_MATRIX_COMPLETE"
        finally:
            transaction.rollback()


def test_actual_declared_source_builder_matches_production_sql(migrated_engine):
    from declared_upload_sql_fixture import actual_basket_statements

    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            for statement in actual_basket_statements():
                result = connection.exec_driver_sql(
                    statement, execution_options={"no_parameters": True}
                )
            assert result.scalar() == "ACTUAL_DECLARED_BASKET_SQL_COMPLETE"
        finally:
            transaction.rollback()


def test_declared_upload_retained_input_versions_are_distinct(migrated_engine):
    from declared_upload_sql_fixture import qualification_statements

    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            for statement in qualification_statements():
                result = connection.exec_driver_sql(
                    statement, execution_options={"no_parameters": True}
                )
            assert result.scalar() == "DECLARED_UPLOAD_QUALIFICATION_MATRIX_COMPLETE"
        finally:
            transaction.rollback()


def test_installed_numeric_bounds_match_strict_python_fields(migrated_engine):
    from declared_upload_numeric_fixture import numeric_statements

    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            for statement in numeric_statements():
                result = connection.exec_driver_sql(
                    statement, execution_options={"no_parameters": True}
                )
            assert result.scalar() == "DECLARED_UPLOAD_NUMERIC_MATRIX_COMPLETE"
        finally:
            transaction.rollback()


def test_installed_signed_child_exit_preserves_unsigned_identity_fields(
    migrated_engine,
):
    from declared_upload_signed_fixture import signed_statements

    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            for statement in signed_statements():
                result = connection.exec_driver_sql(
                    statement, execution_options={"no_parameters": True}
                )
            assert result.scalar() == "DECLARED_UPLOAD_SIGNED_MATRIX_COMPLETE"
        finally:
            transaction.rollback()


def test_workflow_optional_controller_matches_retained_canonical(migrated_engine):
    from workflow_controller_selector_fixture import statements

    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            for statement in statements():
                result = connection.exec_driver_sql(
                    statement, execution_options={"no_parameters": True}
                )
            assert result.scalar() == "WORKFLOW_CONTROLLER_SELECTOR_COMPLETE"
        finally:
            transaction.rollback()


def test_oe_append_fence_repair_preserves_current_attempt_guards(migrated_engine):
    from oe_append_fence_fixture import isolation_statements, statements

    for isolation, fixture, marker in (
        ("READ COMMITTED", statements, "OE032B_APPEND_MATRIX_COMPLETE"),
        ("REPEATABLE READ", isolation_statements, "OE032B_ISOLATION_REJECTED"),
    ):
        with migrated_engine.connect().execution_options(
            isolation_level=isolation
        ) as connection:
            transaction = connection.begin()
            try:
                for statement in fixture():
                    result = connection.exec_driver_sql(
                        statement, execution_options={"no_parameters": True}
                    )
                assert result.scalar() == marker
            finally:
                transaction.rollback()
