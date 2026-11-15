"""Freeze the additive032g one-predicate experiment from pinned predecessors.

This is a new reconstruction, not a recovery of an earlier reviewed Git tree.
No earlier migration is rewritten; production approval requires real PG/native CI.
"""

import ast
import hashlib
from pathlib import Path

from backend.app.rdagent_provider_revision import function, literal
from script import schema_keyset_candidate as candidate

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PINS = {
    "032f_shared_validation_cost.py": "89c7a67791dcf64aadc52a010cb653ffad21b66b475a49fdbc5d43c433345c84",
    "025_structured_factor_strategy.py": "7f6baa14133c691fddb22dba993e461cfc7100c396590d653038bbbc77d6c530",
}

RUNTIME = r'''


def _quote(schema):
    if type(schema) is not str or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema) is None:
        raise RuntimeError("Exact installation schema required")
    return '"' + schema + '"'


def _body(sql, schema):
    return (
        sql.split("AS $$", 1)[1].rsplit("$$;", 1)[0].replace("@SCHEMA@", _quote(schema))
    )


def _snapshot(connection, schema):
    # Overloads are included intentionally: an unexpected one is not a predecessor.
    return [
        dict(row)
        for row in connection.exec_driver_sql(
            """
        SELECT p.oid,p.proname,p.pronamespace,p.proowner,p.prolang,l.lanname,
          p.procost,p.prorows,p.provariadic,p.prosupport,p.prokind,p.prosecdef,
          p.proleakproof,p.proisstrict,p.proretset,p.provolatile,p.proparallel,
          p.pronargs,p.pronargdefaults,p.prorettype,p.proargtypes::pg_catalog.text AS proargtypes,
          p.proallargtypes,p.proargmodes,p.proargnames,
          pg_catalog.pg_get_expr(p.proargdefaults,0) AS defaults,
          p.protrftypes,p.prosrc,p.probin,p.prosqlbody::pg_catalog.text AS prosqlbody,
          p.proconfig,p.proacl::pg_catalog.text AS proacl,
          pg_catalog.pg_has_role(CURRENT_USER,p.proowner,'USAGE') AS owner_usable
        FROM pg_catalog.pg_proc p
        JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
        JOIN pg_catalog.pg_language l ON l.oid=p.prolang
        WHERE n.nspname=%s AND p.proname=ANY(%s)
        ORDER BY p.proname,p.oid
        """,
            (schema, list(FUNCTION_SOURCES)),
        )
        .mappings()
        .all()
    ]


def _validate_functions(rows, schema, main_sql):
    if len(rows) != len(FUNCTION_SOURCES) or {r["proname"] for r in rows} != set(
        FUNCTION_SOURCES
    ):
        raise RuntimeError("Exact key-set predecessor function inventory required")
    common = {
        "lanname": "plpgsql",
        "procost": 100.0,
        "prorows": 0.0,
        "provariadic": 0,
        "prosupport": 0,
        "prokind": "f",
        "prosecdef": False,
        "proleakproof": False,
        "proisstrict": False,
        "proretset": False,
        "provolatile": "i",
        "proparallel": "u",
        "prorettype": 16,
        "proallargtypes": None,
        "proargmodes": None,
        "protrftypes": None,
        "probin": None,
        "prosqlbody": None,
        "proconfig": ["search_path=pg_catalog"],
    }
    for row in rows:
        name = row["proname"]
        schema_function = name != "mineagent_structured_json_value"
        expected = common | {
            "pronargs": 4 if schema_function else 2,
            "pronargdefaults": 1 if schema_function else 0,
            "proargtypes": "3802 3802 3802 23" if schema_function else "3802 23",
            "proargnames": ["v", "rule", "defs", "depth"]
            if schema_function
            else ["v", "depth"],
            "defaults": "0" if schema_function else None,
            "prosrc": _body(
                main_sql if name == "mineagent_rd_schema" else FUNCTION_SOURCES[name],
                schema,
            ),
        }
        if any(row.get(key) != value for key, value in expected.items()):
            raise RuntimeError("Exact key-set predecessor definition required: " + name)
        # CREATE OR REPLACE needs usable ownership only for the replaced main.
        # Helpers may have unrelated owners. Preserve those owners/ACLs/OIDs.
        if name == "mineagent_rd_schema" and row.get("owner_usable") is not True:
            raise RuntimeError("Usable ownership of mineagent_rd_schema required")


def _platform(connection):
    row = (
        connection.exec_driver_sql("""SELECT
        pg_catalog.current_setting('server_version_num')::pg_catalog.integer AS version,
        pg_catalog.current_setting('server_encoding') AS encoding,
        (SELECT collisdeterministic FROM pg_catalog.pg_collation
         WHERE oid=100 AND collname='default' AND collnamespace=11) AS deterministic,
        (SELECT pg_catalog.array_agg(t.typname::pg_catalog.text ORDER BY t.oid)
         FROM pg_catalog.pg_type t WHERE t.typnamespace=11 AND t.oid IN (16,23,3802)) AS types
        """)
        .mappings()
        .one()
    )
    # PostgreSQL18 is the deployment contract, not an observed CI patch version.
    # Canonical JSONB key order/equality is not UTF8-specific. Do not narrow encoding.
    if (
        not 180000 <= row["version"] < 190000
        or row["deterministic"] is not True
        or row["types"] != ["bool", "int4", "jsonb"]
    ):
        raise RuntimeError("Supported PostgreSQL18 semantic catalog required")
    # pg_temp can implicitly precede pg_catalog for PL/pgSQL declaration types,
    # even inside a function with SET search_path=pg_catalog. Reject type shadows
    # at installation. Application-schema names are excluded by the safe path.
    shadows = (
        connection.exec_driver_sql(
            """SELECT t.typname
        FROM pg_catalog.pg_type t
        WHERE t.typnamespace=pg_catalog.pg_my_temp_schema()
          AND t.typname=ANY(%s)
        """,
            (list(DECLARATION_TYPES),),
        )
        .scalars()
        .all()
    )
    if shadows:
        raise RuntimeError("Temporary declaration type shadow is unsafe")


def _maintenance(connection, schema):
    connection.exec_driver_sql("""DO $$ BEGIN
        IF pg_catalog.current_setting('transaction_isolation') <> 'read committed'
        OR NOT pg_catalog.pg_try_advisory_xact_lock(1413960260,1129270340)
        THEN RAISE EXCEPTION 'Key-set migration requires quiescent maintenance'; END IF;
        END $$;""")
    connection.exec_driver_sql(
        "LOCK TABLE "
        + ",".join(_quote(schema) + "." + name for name in TABLES)
        + " IN ACCESS EXCLUSIVE MODE NOWAIT"
    )


def _apply(sql, expected_revision):
    connection = op.get_bind()
    schema = connection.exec_driver_sql(
        "SELECT pg_catalog.current_schema()"
    ).scalar_one()
    quoted = _quote(schema)
    old_path = connection.exec_driver_sql("SHOW search_path").scalar_one()
    _maintenance(connection, schema)
    try:
        connection.exec_driver_sql("SET LOCAL search_path = pg_catalog, pg_temp")
        _platform(connection)
        versions = (
            connection.exec_driver_sql(
                "SELECT version_num FROM " + quoted + ".alembic_version"
            )
            .scalars()
            .all()
        )
        if versions != [expected_revision]:
            raise RuntimeError("Exact key-set migration revision required")
        previous_sql = (
            DOWNGRADE_SQL if expected_revision == down_revision else UPGRADE_SQL
        )
        before = _snapshot(connection, schema)
        _validate_functions(before, schema, previous_sql)
        connection.exec_driver_sql(
            sql.replace("@SCHEMA@", quoted), execution_options={"no_parameters": True}
        )
        after = _snapshot(connection, schema)
        _validate_functions(after, schema, sql)
        # Even unchanged helpers must retain all metadata, bodies and identities.
        for old, new in zip(before, after, strict=True):
            expected = dict(old)
            if old["proname"] == "mineagent_rd_schema":
                expected["prosrc"] = _body(sql, schema)
            if new != expected:
                raise RuntimeError(
                    "Key-set migration changed function identity or attributes"
                )
    finally:
        # Do not mask the original SQLSTATE with a query in a failed transaction.
        # Alembic owns rollback; on success/local preflight rejection restore path.
        raw = connection.connection.driver_connection
        if raw.info.transaction_status != 3:  # psycopg TransactionStatus.INERROR
            connection.exec_driver_sql(
                "SELECT pg_catalog.set_config('search_path',%s,true)", (old_path,)
            )


def upgrade():
    _apply(UPGRADE_SQL, down_revision)


def downgrade():
    _apply(DOWNGRADE_SQL, revision)
'''


def document():
    sources = {}
    for filename, pin in SOURCE_PINS.items():
        data = (ROOT / "backend/alembic/versions" / filename).read_bytes()
        if hashlib.sha256(data).hexdigest() != pin:
            raise ValueError("Frozen predecessor source differs: " + filename)
        sources[filename] = data.decode()
    tree = ast.parse(sources["032f_shared_validation_cost.py"])
    maintenance = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_maintenance"
    )
    assignment = next(
        n
        for n in ast.walk(maintenance)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "tables" for t in n.targets)
    )
    tables = ast.literal_eval(assignment.value)
    if len(tables) != 187 or len(set(tables)) != 187:
        raise ValueError("Exact predecessor maintenance table inventory required")
    functions = {
        candidate.NAME: candidate.restore_schema_sql(),
        candidate.HELPER: candidate.dependencies_sql(),
        "mineagent_structured_json_value": function(
            literal("025_structured_factor_strategy.py"),
            "mineagent_structured_json_value",
        ),
    }
    header = '''"""Reduce only exact JSONB key-set comparison cost.

Frozen additive single-predicate experiment. Requires quiescent maintenance.
Real PostgreSQL18/native validation and separate promotion approval are required.
"""

import re

from alembic import op

revision = "032g_schema_keyset_cost"
down_revision = "032f_shared_validation_cost"
branch_labels = None
depends_on = None

# fmt: off
'''
    constants = {
        "PREDECESSOR_SOURCE_PINS": SOURCE_PINS,
        "TABLES": tables,
        "DECLARATION_TYPES": (
            "record",
            "jsonb",
            "text",
            "integer",
            "int4",
            "numeric",
            "float8",
            "double precision",
            "boolean",
            "bool",
            "date",
            "timestamptz",
        ),
        "FUNCTION_SOURCES": functions,
        "UPGRADE_SQL": candidate.schema_sql(),
        "DOWNGRADE_SQL": candidate.restore_schema_sql(),
    }
    for name, value in constants.items():
        header += "\n" + name + " = " + repr(value) + "\n"
    return header + "\n# fmt: on" + RUNTIME


if __name__ == "__main__":
    (ROOT / "backend/alembic/versions/032g_schema_keyset_cost.py").write_text(
        document()
    )
