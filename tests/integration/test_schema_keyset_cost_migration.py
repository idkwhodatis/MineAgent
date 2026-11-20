"""Real032g install, exact predecessor refusal, parity, locks and rollback.

These cases require an already approved PostgreSQL18 service. Collection is not
execution evidence; no server is provisioned by this module.
"""

import importlib.util
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import event
from sqlalchemy.exc import DBAPIError

from script import schema_keyset_candidate as candidate
from tests.integration.test_rdagent_validation_performance import schema_pair
from tests.integration.test_rdagent_validation_performance import (
    test_full_config_structurals_and_negative_mutations as full_corpus,
)
from tests.integration.test_rdagent_validation_performance import (
    test_schema_union_branch_recovery_and_malformed_rules as malformed_corpus,
)
from tests.integration.test_schema_reference_cost import (
    test_reference_depth_boundary_chains_cycles_and_bad_targets as reference_corpus,
)
from tests.integration.test_shared_validation_cost_migration import configuration, state

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]
CURRENT = "032g_schema_keyset_cost"
PREVIOUS = "032f_shared_validation_cost"


def migration():
    spec = importlib.util.spec_from_file_location(
        "schema032g", ROOT / "backend/alembic/versions/032g_schema_keyset_cost.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def revision(connection):
    return (
        connection.exec_driver_sql("SELECT version_num FROM alembic_version")
        .scalars()
        .all()
    )


def snapshot(connection):
    schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
    return (
        migration()._snapshot(connection, schema),
        state(connection),
        revision(connection),
    )


def assert_delta(before, after, module, schema, target):
    assert len(before[0]) == len(after[0]) == 3
    for old, new in zip(before[0], after[0], strict=True):
        expected = dict(old)
        if old["proname"] == candidate.NAME:
            expected["prosrc"] = module._body(
                module.UPGRADE_SQL if target == CURRENT else module.DOWNGRADE_SQL,
                schema,
            )
        assert new == expected
    old_functions, *old_rest = before[1]
    new_functions, *new_rest = after[1]
    assert old_rest == new_rest
    assert set(old_functions) == set(new_functions)
    assert {
        key for key in old_functions if old_functions[key] != new_functions[key]
    } == {key for key in old_functions if key.startswith(candidate.NAME + "(")}
    assert after[2] == [target]


def test_full_chain_install_downgrade_preserves_identity_acl_defaults_and_all_graphs(
    migrated_engine,
):
    module = migration()
    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
            assert revision(connection) == [CURRENT]
            installed = snapshot(connection)
            module._validate_functions(installed[0], schema, module.UPGRADE_SQL)
            command.downgrade(configuration(connection), PREVIOUS)
            predecessor = snapshot(connection)
            assert_delta(installed, predecessor, module, schema, PREVIOUS)
            command.upgrade(configuration(connection), CURRENT)
            assert_delta(predecessor, snapshot(connection), module, schema, CURRENT)
            assert snapshot(connection) == installed
        finally:
            transaction.rollback()


@pytest.mark.parametrize(
    "name", [candidate.NAME, candidate.HELPER, "mineagent_structured_json_value"]
)
@pytest.mark.parametrize("drift", ["body", "attribute", "overload"])
def test_predecessor_definition_drift_rejected_atomically(migrated_engine, name, drift):
    module = migration()
    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            command.downgrade(configuration(connection), PREVIOUS)
            schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
            quoted = module._quote(schema)
            args = (
                "jsonb,integer"
                if name == "mineagent_structured_json_value"
                else "jsonb,jsonb,jsonb,integer"
            )
            if drift == "body":
                sql = module.FUNCTION_SOURCES[name]
                # Retain every declaration but change the exact frozen body.
                sql = sql.split("AS $$", 1)[0] + "AS $$ BEGIN RETURN false; END $$;"
                connection.exec_driver_sql(
                    sql.replace("@SCHEMA@", quoted),
                    execution_options={"no_parameters": True},
                )
            elif drift == "attribute":
                connection.exec_driver_sql(
                    f"ALTER FUNCTION {quoted}.{name}({args}) COST 101"
                )
            else:
                connection.exec_driver_sql(
                    f"CREATE FUNCTION {quoted}.{name}(pg_catalog.text) RETURNS pg_catalog.bool LANGUAGE sql AS 'SELECT false'"
                )
            before = snapshot(connection)
            with (
                pytest.raises(RuntimeError, match="predecessor"),
                connection.begin_nested(),
            ):
                command.upgrade(configuration(connection), CURRENT)
            assert snapshot(connection) == before
            assert revision(connection) == [PREVIOUS]
        finally:
            transaction.rollback()


def test_application_shadows_and_nondefault_acl_preserved(migrated_engine):
    module = migration()
    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            command.downgrade(configuration(connection), PREVIOUS)
            schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
            q = module._quote(schema)
            connection.exec_driver_sql(
                f"REVOKE EXECUTE ON FUNCTION {q}.mineagent_rd_schema(jsonb,jsonb,jsonb,integer) FROM PUBLIC"
            )
            connection.exec_driver_sql(
                f"CREATE FUNCTION {q}.jsonb_object_keys(pg_catalog.jsonb) RETURNS SETOF pg_catalog.text LANGUAGE sql AS 'SELECT ''unsafe''::pg_catalog.text'"
            )
            connection.exec_driver_sql(f"CREATE DOMAIN {q}.jsonb AS pg_catalog.text")
            before = snapshot(connection)
            old_path = connection.exec_driver_sql("SHOW search_path").scalar_one()
            command.upgrade(configuration(connection), CURRENT)
            assert (
                connection.exec_driver_sql("SHOW search_path").scalar_one() == old_path
            )
            assert_delta(before, snapshot(connection), module, schema, CURRENT)
            assert (
                connection.exec_driver_sql(
                    f"SELECT {q}.mineagent_rd_schema('{{}}'::pg_catalog.jsonb,'{{\"type\":\"object\",\"properties\":{{}}}}'::pg_catalog.jsonb,'{{}}'::pg_catalog.jsonb,0)"
                ).scalar_one()
                is True
            )
            command.downgrade(configuration(connection), PREVIOUS)
            assert snapshot(connection) == before
        finally:
            transaction.rollback()


def test_temporary_type_shadow_refused_without_revision_or_catalog_change(
    migrated_engine,
):
    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            command.downgrade(configuration(connection), PREVIOUS)
            connection.exec_driver_sql(
                "CREATE TEMP TABLE declaration_shadow_anchor (x integer) ON COMMIT DROP"
            )
            connection.exec_driver_sql("CREATE DOMAIN pg_temp.jsonb AS pg_catalog.text")
            before = snapshot(connection)
            with (
                pytest.raises(RuntimeError, match="Temporary declaration type"),
                connection.begin_nested(),
            ):
                command.upgrade(configuration(connection), CURRENT)
            assert snapshot(connection) == before
        finally:
            transaction.rollback()


@pytest.mark.parametrize("guard", ["isolation", "advisory", "table"])
def test_both_directions_require_all_maintenance_guards(migrated_engine, guard):
    for direction, target in (("downgrade", PREVIOUS), ("upgrade", CURRENT)):
        with migrated_engine.connect() as inspect:
            before = snapshot(inspect)
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
                        assert caught.value.orig.sqlstate == (
                            "55P03" if guard == "table" else "P0001"
                        )
                    finally:
                        transaction.rollback()
            finally:
                blocking.rollback()
        with migrated_engine.begin() as connection:
            assert snapshot(connection) == before
            getattr(command, direction)(configuration(connection), target)


def test_uncommitted_replacement_excludes_readers_and_writers(migrated_engine):
    with migrated_engine.connect() as installation:
        transaction = installation.begin()
        try:
            command.downgrade(configuration(installation), PREVIOUS)
            for sql in (
                "SELECT count(*) FROM research_runs",
                "UPDATE research_runs SET status=status WHERE false",
            ):
                with migrated_engine.connect() as reader:
                    blocked = reader.begin()
                    try:
                        reader.exec_driver_sql("SET LOCAL lock_timeout='50ms'")
                        with pytest.raises(DBAPIError) as caught:
                            reader.exec_driver_sql(sql)
                        assert caught.value.orig.sqlstate == "55P03"
                    finally:
                        blocked.rollback()
        finally:
            transaction.rollback()


def test_failure_after_actual_replacement_rolls_back_both_directions(migrated_engine):
    for direction, target in (("downgrade", PREVIOUS), ("upgrade", CURRENT)):
        with migrated_engine.connect() as connection:
            transaction = connection.begin()
            try:
                before = snapshot(connection)
                fired = []

                def fail_after_sql(
                    conn,
                    cursor,
                    statement,
                    parameters,
                    context,
                    executemany,
                    fired=fired,
                ):
                    if (
                        statement.startswith("CREATE OR REPLACE FUNCTION")
                        and ".mineagent_rd_schema(" in statement
                    ):
                        fired.append(True)
                        raise RuntimeError("injected after actual replacement")

                event.listen(connection, "after_cursor_execute", fail_after_sql)
                try:
                    with (
                        pytest.raises(
                            RuntimeError, match="injected after actual replacement"
                        ),
                        connection.begin_nested(),
                    ):
                        getattr(command, direction)(configuration(connection), target)
                finally:
                    event.remove(connection, "after_cursor_execute", fail_after_sql)
                assert fired == [True]
                assert snapshot(connection) == before
                getattr(command, direction)(configuration(connection), target)
                transaction.commit()
            finally:
                if transaction.is_active:
                    transaction.rollback()


def test_installed_successor_full_differential_corpus_and_large_keysets(
    migrated_engine,
):
    with migrated_engine.begin() as connection:
        assert revision(connection) == [CURRENT]
        schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
        connection.exec_driver_sql(
            candidate.baseline_schema_sql().replace("@SCHEMA@", '"' + schema + '"'),
            execution_options={"no_parameters": True},
        )
    full_corpus(migrated_engine)
    malformed_corpus(migrated_engine)
    reference_corpus(migrated_engine)
    with migrated_engine.begin() as connection:
        properties = {f"k{i:05d}": {"type": "null"} for i in range(4096)}
        value = dict.fromkeys(reversed(properties))
        rule = {"type": "object", "properties": properties}
        assert schema_pair(connection, value, rule) == ("ok", True)
        value["extra"] = None
        assert schema_pair(connection, value, rule) == ("ok", False)
        for depth in (None, -1, 0, 23, 24, 25):
            schema_pair(connection, {}, {"type": "object"}, depth=depth)
