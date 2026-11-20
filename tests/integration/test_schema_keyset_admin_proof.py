"""Opt-in owned-service PostgreSQL 18 role and LATIN1 proofs.

Collection does no I/O. Roles, grants, tables and schemas live exclusively in
one rolled-back transaction. The one created database has a separately guarded,
identity-bound cleanup. These tests never use an arbitrary TEST_DATABASE_URL.
"""

import json
import os
from contextlib import contextmanager
from importlib import import_module
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from script import schema_keyset_admin_proof as proof
from script import schema_keyset_candidate as candidate

pytestmark = pytest.mark.postgres
SQL_NULL = object()


@pytest.fixture
def owned_keyset_admin():
    if "MINEAGENT_KEYSET_ADMIN_PROOF" not in os.environ:
        pytest.skip("Explicit owned CI PostgreSQL admin proof opt-in required")
    with proof.owned_admin() as admin:
        yield admin


def migration():
    return import_module("backend.alembic.versions.032g_schema_keyset_cost")


@contextmanager
def rollback_fixture(admin, engine=None):
    admin.verify()
    with (engine or admin.engine).connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            # Rollback is connection-bound and remains mandatory even if an
            # identity recheck refuses all new cleanup operations.
            try:
                admin.verify()
            finally:
                transaction.rollback()


def install_predecessor(connection, *, full_chain=False):
    module = migration()
    schema = "ma032g_proof_" + uuid4().hex
    connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    connection.exec_driver_sql(f'SET LOCAL search_path="{schema}",pg_catalog')
    if full_chain:
        root = proof.repository_root()
        config = Config(str(root / "backend/alembic.ini"))
        config.set_main_option("script_location", str(root / "backend/alembic"))
        config.attributes["connection"] = connection
        command.upgrade(config, module.down_revision)
        assert revision(connection, schema) == module.down_revision
        return schema
    # Focused role-only fixture; the LATIN1 case uses the genuine full chain.
    for name in module.TABLES:
        connection.exec_driver_sql(f'CREATE TABLE "{schema}"."{name}" (id integer)')
    connection.exec_driver_sql(
        f'CREATE TABLE "{schema}".alembic_version (version_num varchar(64) PRIMARY KEY)'
    )
    connection.execute(
        text(f'INSERT INTO "{schema}".alembic_version VALUES (:revision)'),
        {"revision": module.down_revision},
    )
    for sql in module.FUNCTION_SOURCES.values():
        connection.exec_driver_sql(
            sql.replace("@SCHEMA@", f'"{schema}"'),
            execution_options={"no_parameters": True},
        )
    return schema


def revision(connection, schema):
    return connection.scalar(
        text(f'SELECT version_num FROM "{schema}".alembic_version')
    )


def snapshot(connection, schema):
    rows = (
        connection.execute(
            text("""
        SELECT p.proname,
               (to_jsonb(p) - 'proargdefaults') || jsonb_build_object(
                   'proargdefaults',pg_get_expr(p.proargdefaults,0)) AS metadata,
               pg_get_functiondef(p.oid) AS definition
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname=:schema AND p.proname IN
        ('mineagent_rd_schema','mineagent_rd_schema_recursive_032e','mineagent_structured_json_value')
        ORDER BY p.proname
    """),
            {"schema": schema},
        )
        .mappings()
        .all()
    )
    assert len(rows) == 3
    return {
        row["proname"]: (dict(row["metadata"]), row["definition"]) for row in rows
    }, revision(connection, schema)


def apply(connection, schema, direction):
    module = migration()
    with Operations.context(MigrationContext.configure(connection)):
        getattr(module, direction)()
    connection.execute(
        text(f'UPDATE "{schema}".alembic_version SET version_num=:revision'),
        {
            "revision": module.revision
            if direction == "upgrade"
            else module.down_revision
        },
    )


def assert_exact_replacement(before, after, direction):
    old, new = (
        (candidate.OLD, candidate.NEW)
        if direction == "upgrade"
        else (candidate.NEW, candidate.OLD)
    )
    module = migration()
    assert before[1] == (
        module.down_revision if direction == "upgrade" else module.revision
    )
    assert after[1] == (
        module.revision if direction == "upgrade" else module.down_revision
    )
    for name, (metadata, definition) in before[0].items():
        expected_metadata = dict(metadata)
        expected_definition = definition
        if name == candidate.NAME:
            assert metadata["prosrc"].count(old) == 1
            expected_metadata["prosrc"] = metadata["prosrc"].replace(old, new)
            expected_definition = definition.replace(old, new)
        assert after[0][name] == (expected_metadata, expected_definition)


def function_signature(name):
    args = (
        "jsonb,integer"
        if name == "mineagent_structured_json_value"
        else "jsonb,jsonb,jsonb,integer"
    )
    return f"{name}({args})"


def create_role(connection):
    name = "ma032g_role_" + uuid4().hex
    connection.exec_driver_sql(
        f'CREATE ROLE "{name}" NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB '
        "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
    )
    return name


def setup_actor(connection, schema, mode):
    actor = create_role(connection)
    main_owner = create_role(connection) if mode in ("inherit", "noninherit") else actor
    connection.exec_driver_sql(f'GRANT USAGE, CREATE ON SCHEMA "{schema}" TO "{actor}"')
    for table in (*migration().TABLES, "alembic_version"):
        connection.exec_driver_sql(
            f'GRANT SELECT, UPDATE ON "{schema}"."{table}" TO "{actor}"'
        )
    if mode in ("inherit", "noninherit"):
        inherit = "TRUE" if mode == "inherit" else "FALSE"
        connection.exec_driver_sql(
            f'GRANT "{main_owner}" TO "{actor}" WITH INHERIT {inherit}, SET FALSE'
        )
    owners = {}
    for name in migration().FUNCTION_SOURCES:
        owner = main_owner
        if name != candidate.NAME and mode in ("foreign_helpers", "noninherit_helpers"):
            owner = create_role(connection)
            if mode == "noninherit_helpers":
                connection.exec_driver_sql(
                    f'GRANT "{owner}" TO "{actor}" WITH INHERIT FALSE, SET FALSE'
                )
        signature = f'"{schema}".{function_signature(name)}'
        connection.exec_driver_sql(f'ALTER FUNCTION {signature} OWNER TO "{owner}"')
        connection.exec_driver_sql(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        connection.exec_driver_sql(
            f'GRANT EXECUTE ON FUNCTION {signature} TO "{actor}"'
        )
        owners[name] = owner
    connection.exec_driver_sql(f'SET LOCAL ROLE "{actor}"')
    attributes = connection.execute(
        text("""
        SELECT current_user, rolsuper, rolcreaterole, rolcreatedb, rolreplication,
               rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname=current_user
    """)
    ).one()
    assert tuple(attributes) == (actor, False, False, False, False, False, False)
    for name, owner in owners.items():
        usage = connection.scalar(
            text("SELECT pg_has_role(current_user,:owner,'USAGE')"), {"owner": owner}
        )
        member = connection.scalar(
            text("SELECT pg_has_role(current_user,:owner,'MEMBER')"), {"owner": owner}
        )
        can_set = connection.scalar(
            text("SELECT pg_has_role(current_user,:owner,'SET')"), {"owner": owner}
        )
        expected = owner == actor or mode == "inherit"
        assert usage is expected
        if owner != actor:
            assert can_set is False
            assert member is (mode in ("inherit", "noninherit", "noninherit_helpers"))
        assert (
            connection.scalar(
                text(
                    "SELECT has_function_privilege(current_user,:signature,'EXECUTE')"
                ),
                {"signature": f'"{schema}".{function_signature(name)}'},
            )
            is True
        )
    return actor


def role_schema_probe(schema):
    return text(f'''
        SELECT "{schema}".mineagent_rd_schema(
            CAST(:value AS jsonb), CAST(:rule AS jsonb),
            CAST(:defs AS jsonb), :depth)
    ''')


@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
@pytest.mark.parametrize(
    "mode", ["direct", "inherit", "noninherit", "foreign_helpers", "noninherit_helpers"]
)
def test_non_superuser_actual_owner_membership_and_helper_independence(
    owned_keyset_admin, direction, mode
):
    with rollback_fixture(owned_keyset_admin) as connection:
        schema = install_predecessor(connection)
        if direction == "downgrade":
            apply(connection, schema, "upgrade")
        actor = setup_actor(connection, schema, mode)
        before = snapshot(connection, schema)
        if mode == "noninherit":
            with (
                pytest.raises((RuntimeError, DBAPIError)) as failure,
                connection.begin_nested(),
            ):
                apply(connection, schema, direction)
            assert "owner" in str(failure.value).lower()
            assert snapshot(connection, schema) == before
        else:
            apply(connection, schema, direction)
            assert_exact_replacement(before, snapshot(connection, schema), direction)
            # Both the direct and recursive unchanged helpers actually execute.
            for depth in (0, -1, None):
                assert (
                    connection.scalar(
                        role_schema_probe(schema),
                        {
                            "value": json.dumps({"café": "ÿ"}, ensure_ascii=False),
                            "rule": json.dumps(
                                {
                                    "type": "object",
                                    "properties": {"café": {"type": "string"}},
                                    "additionalProperties": False,
                                },
                                ensure_ascii=False,
                            ),
                            "defs": "{}",
                            "depth": depth,
                        },
                    )
                    is True
                )
            assert (
                connection.scalar(
                    text(f"""
                SELECT "{schema}".mineagent_rd_schema('"ÿ"'::jsonb,
                    '{{"$ref":"#/$defs/café"}}'::jsonb,
                    '{{"café":{{"type":"string"}}}}'::jsonb,-1)
            """)
                )
                is True
            )
            assert (
                connection.scalar(
                    text(f"""
                SELECT "{schema}".mineagent_structured_json_value('{{"café":"ÿ"}}'::jsonb,0)
            """)
                )
                is True
            )
        assert connection.scalar(text("SELECT current_user")) == actor


def encoded(value):
    return None if value is SQL_NULL else json.dumps(value, ensure_ascii=False)


def latin1_cases():
    keys = ["", "a", "aa", "é", "café", "ÿ", "£"]
    for left in keys:
        for right in keys:
            yield (
                {left: "ÿ"},
                {
                    "type": "object",
                    "properties": {right: {"type": "string"}},
                    "additionalProperties": False,
                },
                {},
                0,
            )
    for value in ({}, {"café": "£"}, SQL_NULL, None, [], 3, True):
        for rule in (
            {"type": "object", "properties": {}, "additionalProperties": False},
            {"type": "object", "additionalProperties": False},
            {
                "type": "object",
                "properties": {"café": {"type": "string"}},
                "additionalProperties": True,
            },
            {"type": "object", "properties": {"café": {"type": "string"}}},
            {"type": "object", "properties": None, "additionalProperties": False},
            {"type": "object", "properties": []},
            {"type": "object", "additionalProperties": "malformed"},
            {"type": "string", "pattern": "["},
            SQL_NULL,
            None,
            [],
            "malformed",
            3,
        ):
            yield value, rule, {}, 0
    for depth in (-1, 0, 23, 24, 25, None):
        for defs in ({"café": {"type": "string"}}, {}, SQL_NULL, None, []):
            yield "ÿ", {"$ref": "#/$defs/café"}, defs, depth
        yield ["£"], {"type": "array", "items": {"type": "string"}}, {}, depth
        yield (
            {"café": "ÿ"},
            {
                "type": "object",
                "properties": {"café": {"type": "string"}},
                "additionalProperties": False,
            },
            {},
            depth,
        )
    yield {}, {"type": "object", "properties": SQL_NULL}, {}, 0


def outcome(connection, schema, name, case):
    value, rule, defs, depth = case
    # SQL NULL properties are represented by absent properties, not a Python
    # sentinel embedded in a JSON object.
    if isinstance(rule, dict) and rule.get("properties") is SQL_NULL:
        rule = {key: value for key, value in rule.items() if key != "properties"}
    try:
        with connection.begin_nested():
            result = connection.scalar(
                text(
                    f'SELECT "{schema}".{name}(CAST(:v AS jsonb),CAST(:r AS jsonb),CAST(:defs AS jsonb),:depth)'
                ),
                {
                    "v": encoded(value),
                    "r": encoded(rule),
                    "defs": encoded(defs),
                    "depth": depth,
                },
            )
        return "ok", result
    except DBAPIError as error:
        return "error", error.orig.sqlstate


def assert_latin1_differential(connection, schema):
    assert connection.scalar(text("SHOW server_encoding")) == "LATIN1"
    keys = ["aa", "a", "é", "café", "ÿ", "£"]
    actual = connection.scalar(
        text("SELECT ARRAY(SELECT jsonb_object_keys(CAST(:value AS jsonb)))"),
        {"value": json.dumps(dict.fromkeys(keys), ensure_ascii=False)},
    )
    assert actual == sorted(
        keys, key=lambda key: (len(key.encode("latin1")), key.encode("latin1"))
    )
    golden_rule = {
        "type": "object",
        "properties": {key: {"type": "string"} for key in ("café", "ÿ", "£")},
        "additionalProperties": False,
    }
    for value, rule, depth, expected in (
        ({"café": "ÿ", "ÿ": "£", "£": "café"}, golden_rule, 0, True),
        ({"café": "ÿ", "ÿ": "£", "extra": "café"}, golden_rule, 0, False),
        ({}, {"type": "object", "additionalProperties": False}, 0, True),
        (
            {},
            {"type": "object", "properties": {}, "additionalProperties": False},
            0,
            True,
        ),
        (SQL_NULL, {"type": "object"}, 0, False),
        ({}, {"type": "object"}, 25, False),
    ):
        for name in ("baseline_rd_schema", candidate.NAME):
            assert outcome(connection, schema, name, (value, rule, {}, depth)) == (
                "ok",
                expected,
            )
    for case in latin1_cases():
        assert outcome(connection, schema, "baseline_rd_schema", case) == outcome(
            connection, schema, candidate.NAME, case
        )


def test_real_latin1_roundtrip_differential_and_replacement_rollback(
    owned_keyset_admin, monkeypatch
):
    with (
        proof.latin1_database(owned_keyset_admin) as engine,
        rollback_fixture(owned_keyset_admin, engine) as connection,
    ):
        schema = install_predecessor(connection, full_chain=True)
        connection.exec_driver_sql(
            candidate.baseline_schema_sql()
            .replace("old_rd_schema", "baseline_rd_schema")
            .replace("@SCHEMA@", f'"{schema}"'),
            execution_options={"no_parameters": True},
        )
        original = snapshot(connection, schema)
        assert_latin1_differential(connection, schema)
        for direction in ("upgrade", "downgrade"):
            before = snapshot(connection, schema)
            real_execute = connection.exec_driver_sql
            replaced = []

            def injected(
                statement, *args, execute=real_execute, seen=replaced, **kwargs
            ):
                result = execute(statement, *args, **kwargs)
                if (
                    "CREATE OR REPLACE FUNCTION" in statement
                    and ".mineagent_rd_schema(" in statement
                ):
                    seen.append(True)
                    raise RuntimeError("injected after successful function replacement")
                return result

            with monkeypatch.context() as patch:
                patch.setattr(connection, "exec_driver_sql", injected)
                with (
                    pytest.raises(RuntimeError, match="injected after successful"),
                    connection.begin_nested(),
                ):
                    apply(connection, schema, direction)
            assert replaced == [True]
            assert snapshot(connection, schema) == before
            assert_latin1_differential(connection, schema)
            apply(connection, schema, direction)
            assert_exact_replacement(before, snapshot(connection, schema), direction)
            assert_latin1_differential(connection, schema)
        assert snapshot(connection, schema) == original
