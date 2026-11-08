"""Real032f migration roundtrip, unchanged graph checks, and maintenance refusal."""

import importlib.util
import re
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]
CURRENT = "032f_shared_validation_cost"
PREVIOUS = "032e_rd_workflow_cost"
ADDED_GUARDS = {
    "outbox_events:oes032f_outbox_delete",
    "outbox_events:oes032f_outbox_identity",
}
CHANGED = {
    "mineagent_structured_raw_json",
    "mineagent_rd_schema",
    "mineagent_rd_schema_recursive_032e",
}


def configuration(connection):
    config = Config(str(ROOT / "backend/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend/alembic"))
    config.attributes["connection"] = connection
    return config


def migration():
    spec = importlib.util.spec_from_file_location(
        "shared032f", ROOT / "backend/alembic/versions/032f_shared_validation_cost.py"
    )
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def state(connection):
    functions = dict(
        connection.exec_driver_sql("""
        SELECT p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')',
               pg_get_functiondef(p.oid)
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname=current_schema() AND p.prokind='f'
    """).all()
    )
    constraints = dict(
        connection.exec_driver_sql("""
        SELECT c.relname || ':' || k.conname, pg_get_constraintdef(k.oid)
        FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=current_schema()
    """).all()
    )
    triggers = dict(
        connection.exec_driver_sql("""
        SELECT c.relname || ':' || t.tgname, pg_get_triggerdef(t.oid)
        FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=current_schema() AND NOT t.tgisinternal
    """).all()
    )
    return functions, constraints, triggers


def assert_bodies(connection, rendered, schema):
    bodies = dict(
        connection.exec_driver_sql("""
        SELECT p.proname,p.prosrc FROM pg_proc p
        JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname=current_schema() AND p.proname IN
          ('mineagent_structured_raw_json','mineagent_rd_schema',
           'mineagent_rd_schema_recursive_032e')
    """).all()
    )
    expected = dict(
        re.findall(
            r"CREATE OR REPLACE FUNCTION @SCHEMA@\.([a-z_0-9]+)\(.*?AS \$\$(.*?)\$\$;",
            rendered,
            re.DOTALL,
        )
    )
    assert expected
    assert bodies == {
        name: body.replace("@SCHEMA@", schema) for name, body in expected.items()
    }


def test_frozen_shared_migration_roundtrip_preserves_all_graph_contracts(
    migrated_engine,
):
    value = migration()
    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            schema = (
                '"'
                + connection.exec_driver_sql("SELECT current_schema()").scalar_one()
                + '"'
            )
            config = configuration(connection)
            before = state(connection)
            assert_bodies(connection, value.UPGRADE_SQL, schema)
            command.downgrade(config, PREVIOUS)
            assert_bodies(connection, value.DOWNGRADE_SQL, schema)
            predecessor = state(connection)
            for previous, current in zip(predecessor[1:], before[1:]):
                assert set(current) - set(previous) == ADDED_GUARDS
                assert previous == {
                    key: definition
                    for key, definition in current.items()
                    if key not in ADDED_GUARDS
                }
            assert {
                k: v
                for k, v in predecessor[0].items()
                if k.split("(", 1)[0] not in CHANGED
            } == {
                k: v for k, v in before[0].items() if k.split("(", 1)[0] not in CHANGED
            }
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).all() == [(PREVIOUS,)]
            command.upgrade(config, CURRENT)
            assert_bodies(connection, value.UPGRADE_SQL, schema)
            assert state(connection) == before
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).all() == [(CURRENT,)]
        finally:
            transaction.rollback()


@pytest.mark.parametrize("guard", ["isolation", "advisory", "table"])
def test_shared_migration_refuses_nonquiescent_upgrade_and_downgrade(
    migrated_engine, guard
):
    # Each direction starts at its genuine predecessor/current revision. A
    # failed migration must preserve both the complete schema and its head.
    for direction, target in (("downgrade", PREVIOUS), ("upgrade", CURRENT)):
        with migrated_engine.connect() as inspect:
            before = state(inspect)
            revision = inspect.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one()
        with migrated_engine.connect() as blocker:
            blocking = blocker.begin()
            try:
                if guard == "advisory":
                    blocker.exec_driver_sql(
                        "SELECT pg_advisory_xact_lock(1413960260,1129270340)"
                    )
                elif guard == "table":
                    blocker.exec_driver_sql(
                        "LOCK TABLE research_runs IN ROW EXCLUSIVE MODE"
                    )
                with migrated_engine.connect() as connection:
                    if guard == "isolation":
                        connection = connection.execution_options(
                            isolation_level="REPEATABLE READ"
                        )
                    transaction = connection.begin()
                    try:
                        with pytest.raises(DBAPIError) as caught:
                            getattr(command, direction)(
                                configuration(connection), target
                            )
                        expected = "55P03" if guard == "table" else "P0001"
                        assert caught.value.orig.sqlstate == expected
                        if guard != "table":
                            assert (
                                "Shared validation cost migration requires quiescent maintenance"
                                in str(caught.value.orig)
                            )
                    finally:
                        transaction.rollback()
            finally:
                blocking.rollback()
        with migrated_engine.connect() as connection:
            assert state(connection) == before
            assert (
                connection.exec_driver_sql(
                    "SELECT version_num FROM alembic_version"
                ).scalar_one()
                == revision
            )
            connection.rollback()
            with connection.begin():
                getattr(command, direction)(configuration(connection), target)
