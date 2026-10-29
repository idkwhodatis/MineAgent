"""Installed032 metadata/rejection contract; actual released engine path is separate."""

import pytest

pytestmark = pytest.mark.postgres


def test_normal_note_rejects_unbacked_references_and_preserves_legacy(migrated_engine):
    from normal_oe_note_sql_fixture import rejection_statements

    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            for statement in rejection_statements():
                result = connection.exec_driver_sql(statement)
            assert result.scalar() == "NORMAL_NOTE_032_REJECTION_MATRIX_COMPLETE"
        finally:
            transaction.rollback()


def test_normal_note_identity_case_and_space_match_python(migrated_engine):
    from normal_oe_note_sql_fixture import unicode_identity_statements

    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            for statement in unicode_identity_statements():
                result = connection.exec_driver_sql(statement)
            assert result.scalar() == "NORMAL_NOTE_UNICODE_MATRIX_COMPLETE"
        finally:
            transaction.rollback()


def test_note_dataset_identity_predicate_matches_typed_python_domain(migrated_engine):
    from normal_oe_note_sql_fixture import identity_dataset_statements

    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            for statement in identity_dataset_statements():
                result = connection.exec_driver_sql(statement)
            assert result.scalar() == "NORMAL_NOTE_DATASET_IDENTITY_MATRIX_COMPLETE"
        finally:
            transaction.rollback()
