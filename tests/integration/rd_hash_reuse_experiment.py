"""EXPERIMENT ONLY: invocation-local hash reuse in disposable full032c fixtures.

The candidate reuses two immutable hashes within one invocation. Every lookup,
predicate, exception and full validation call remains in the frozen function.
"""

import re
from hashlib import sha256

from backend.app.openevolve_revision_ddl import _function, historical

FUNCTION = "mineagent_rd_workflow_submission_check"
PINS = {
    "029": "2848ba2747b703965ad8de8048591ac1931854666cda2f1f6dd512976aaf9ed3",
    "030": "1f587094ed8d30048865a13f77effdf162ec77b8c5488d70a64c7b1e94c38a7e",
    "031": "67c916d766057c38db8f9e275de1936dcc3edf7a9803931dff597ed6b912ec84",
    "032": "0ccef3532943be981d5238168dee0ad411a31778f9d2e36cc1dd768de3944da9",
    "032a": "174de6e6032ef3bbcccbfb5a3267e3ba0ec7621876e52a3e1c0cb4c681dc59fe",
    "032b": "3b69896e96b2c76c31ce1a883b407e2cb1b0e6e7f4796c94c9574e42169d12b3",
    "032c": "9850df77b05c3866a34bca22182d690faa6056726083080882a6af4dab3f0285",
}
DECLARATION = " cfg jsonb; bind jsonb;"
LOCALS = " cfg_hash text; input_hash text; cfg jsonb; bind jsonb;"
ENTRY = " IF mineagent_rd_workflow_config(cfg) IS NOT TRUE"
ASSIGNMENTS = (
    " cfg_hash:=mineagent_execution_metadata_sha(cfg); "
    "input_hash:=mineagent_execution_metadata_sha(cfg->'input');\n"
)
CONFIG_HASH = "mineagent_execution_metadata_sha(cfg)"
INPUT_HASH = "mineagent_execution_metadata_sha(cfg->'input')"


def previous_function():
    sources = {revision: historical(revision, "UPGRADE_SQL") for revision in PINS}
    for revision, expected in PINS.items():
        if sha256(sources[revision].encode()).hexdigest() != expected:
            raise ValueError("Exact predecessor SQL changed")
        if revision != "029" and re.search(
            r"CREATE (?:OR REPLACE )?FUNCTION (?:@SCHEMA@\.)?" + FUNCTION + r"\(",
            sources[revision],
        ):
            raise ValueError("Unexpected submission predicate replacement")
    return _function(sources["029"], FUNCTION)


def candidate_sql():
    old = previous_function()
    for expression, count in (
        (DECLARATION, 1),
        (ENTRY, 1),
        (CONFIG_HASH, 4),
        (INPUT_HASH, 2),
    ):
        if old.count(expression) != count:
            raise ValueError("Exact hash-expression layout changed")
    return (
        old.replace(DECLARATION, LOCALS)
        .replace(CONFIG_HASH, "cfg_hash")
        .replace(INPUT_HASH, "input_hash")
        .replace(ENTRY, ASSIGNMENTS + ENTRY)
    )


def restoration_sql():
    return previous_function()


# This module is never imported by application/worker/migration code.
import json
import os
from time import monotonic

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

ENV = "MINEAGENT_QLIB_HASH_EXPERIMENT"
REVISION = "032c_oe_provider_table_checks"
CATALOG = text("""
SELECT p.prosrc, l.lanname, p.provolatile, p.proparallel, p.proisstrict,
 p.prosecdef, p.proleakproof, p.proretset, p.prokind, p.procost, p.prorows,
 p.proconfig, p.prorettype::regtype::text, p.proargtypes::text,
 p.proargnames, p.proowner, p.proacl::text, p.oid, p.prosupport::text
FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
JOIN pg_catalog.pg_language l ON l.oid=p.prolang
WHERE n.nspname='public' AND p.proname=:name
""")


def mode():
    value = os.environ.get(ENV)
    if value not in (None, "control", "candidate"):
        raise ValueError("Invalid explicit hash experiment mode")
    return value


def _body(sql):
    return sql.split(" AS $$", 1)[1].rsplit("$$;", 1)[0]


def _catalog(connection):
    rows = connection.execute(CATALOG, {"name": FUNCTION}).all()
    if len(rows) != 1:
        raise ValueError("Exact experiment function identity required")
    row = tuple(rows[0])
    # Exact original declarative defaults, not merely before/after equality.
    if (
        row[1:15]
        != (
            "plpgsql",
            "v",
            "u",
            False,
            False,
            False,
            False,
            "f",
            100,
            0,
            ["search_path=public, pg_catalog"],
            "void",
            "2950",
            ["sid"],
        )
        or row[16] is not None
        or row[18] != "-"
    ):
        raise ValueError("Experiment function catalog drift")
    return row


def _fixture(connection, engine, fixture):
    name = fixture.source.database
    if (
        not re.fullmatch(r"tradegrad_backup_test_[a-f0-9]{32}", name or "")
        or engine.url
        != fixture.source.set(
            port=5432 if fixture.source.port is None else fixture.source.port
        )
        or connection.exec_driver_sql("SELECT current_database()").scalar_one() != name
        or connection.exec_driver_sql("SELECT version_num FROM public.alembic_version")
        .scalars()
        .all()
        != [REVISION]
    ):
        raise ValueError("Full032c disposable fixture identity required")


def prepare(engine, fixture, *, policy=None):
    """Return closed provenance, replacing only the pinned disposable function."""
    selected = mode()
    if selected is None:
        return None
    if policy not in ("default-off-waits", "explicit-opt-in-two-children"):
        raise ValueError("Closed experiment policy required")
    old, new = previous_function(), candidate_sql()
    with engine.begin() as connection:
        _fixture(connection, engine, fixture)
        before = _catalog(connection)
        if before[0] != _body(old):
            raise ValueError("Installed original experiment body drift")
        if selected == "candidate":
            connection.exec_driver_sql(
                new.replace("@SCHEMA@", '"public"'),
                execution_options={"no_parameters": True},
            )
        after = _catalog(connection)
        if (
            after[0] != _body(new if selected == "candidate" else old)
            or after[1:] != before[1:]
        ):
            raise ValueError("Installed experiment replacement drift")
    return {
        "mode": selected,
        "policy": policy,
        "schema_revision": REVISION,
        "schema_state": "full032c_"
        + (
            "experimental_submission_hash_reuse"
            if selected == "candidate"
            else "unmodified"
        ),
        "migration_sql_sha256": dict(PINS),
        "original_body_sha256": sha256(_body(old).encode()).hexdigest(),
        "installed_body_sha256": sha256(after[0].encode()).hexdigest(),
        "catalog_properties_verified": True,
        "production_acceptance": False,
        "started": monotonic(),
    }


def probes(engine, fixture, *, receipt=None, phase=None, run_id=None):
    """Real populated-row rejection probes; not whole-graph equivalence proof.

    No trigger/constraint is disabled. An earlier immutable guard may reject a
    mutation before submission_check; only the successful baseline and missing
    row probe necessarily call that function directly. All writes roll back.
    """
    if mode() is None:
        return
    if receipt is None or phase not in ("first", "seeded"):
        raise ValueError("Closed experiment probe context required")
    outcomes = []
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            _fixture(connection, engine, fixture)
            sid = connection.execute(
                text(
                    "SELECT id FROM public.factor_workflow_submissions WHERE run_id=:run_id"
                ),
                {"run_id": run_id},
            ).scalar_one()
            connection.execute(
                text("SELECT public.mineagent_rd_workflow_submission_check(:sid)"),
                {"sid": sid},
            )
            probes = (
                (
                    "missing_submission",
                    "SELECT public.mineagent_rd_workflow_submission_check('00000000-0000-0000-0000-000000000000'::uuid)",
                ),
                (
                    "config_commitment",
                    "UPDATE public.factor_workflow_submissions SET config_sha256=repeat('0',64) WHERE id=:sid",
                ),
                (
                    "selection_commitment",
                    "UPDATE public.factor_workflow_submissions SET selection_sha256=repeat('0',64) WHERE id=:sid",
                ),
            )
            for label, statement in probes:
                savepoint = connection.begin_nested()
                code = None
                try:
                    connection.execute(text(statement), {"sid": sid})
                    connection.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
                except DBAPIError as exc:
                    code = getattr(exc.orig, "sqlstate", None)
                finally:
                    savepoint.rollback()
                if code not in ("P0001", "P0002", "23514"):
                    raise ValueError("Experiment rejection probe failed")
                outcomes.append({"probe": label, "sqlstate": code})
        finally:
            transaction.rollback()
    print(
        "QLIB_HASH_PROBES:"
        + json.dumps(
            {
                "mode": mode(),
                "policy": receipt["policy"],
                "phase": phase,
                "baseline": "accepted",
                "rejections": outcomes,
            },
            sort_keys=True,
        ),
        flush=True,
    )


def finish(receipt):
    if receipt is not None:
        receipt = dict(receipt)
        receipt["elapsed_seconds"] = round(monotonic() - receipt.pop("started"), 6)
        print("QLIB_HASH_EXPERIMENT:" + json.dumps(receipt, sort_keys=True), flush=True)
