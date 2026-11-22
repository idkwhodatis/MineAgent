"""Opt-in, disposable PostgreSQL A/B harness. Never imported by runtime code."""

import re

from tests.sql_probes import rd_graph_canonical_candidate as probe

_ARMS = {"baseline", "ascii_reuse"}
_NAMESPACE = re.compile("tradegrad_backup_test_[a-f0-9]{32}")


def validate_arm(arm, allow_feedback_release, env):
    if arm not in _ARMS or allow_feedback_release is not True:
        raise ValueError("Select one explicit opt-in Qlib A/B arm only")
    required = {
        "RUN_BACKUP_RESTORE_DRILL": "1",
        "DATA_PROVIDER_MODE": "fixture",
        "MODEL_PROFILES_ENABLED": "false",
        "E2E_LIVE_PROVIDERS": "0",
        "MINEAGENT_QLIB_RPC_COST": "1",
    }
    if (
        any(env.get(k) != v for k, v in required.items())
        or env.get("MINEAGENT_QLIB_WAIT_PROFILE", "0") != "0"
    ):
        raise ValueError("Require isolated synthetic-provider Qlib A/B configuration")


def _verify_database(connection, databases, *, restored=False):
    source, target = databases.source, databases.restored
    if (
        source.database == target.database
        or not all(_NAMESPACE.fullmatch(v.database or "") for v in (source, target))
        or source.set(database=target.database) != target
    ):
        raise ValueError("Require a fresh pair of generated drill databases")
    expected = target if restored else source
    if connection.engine.url != expected.set(port=expected.port or 5432):
        raise ValueError("Connection does not use the owned drill database")
    database, schema, version, owns_database = connection.exec_driver_sql(
        """SELECT current_database(), current_schema(),
                  current_setting('server_version_num'),
                  d.datdba = (SELECT oid FROM pg_roles WHERE rolname=current_user)
           FROM pg_database AS d WHERE d.datname=current_database()"""
    ).one()
    if (
        database != expected.database
        or schema != "public"
        or version != "180006"
        or owns_database is not True
    ):
        raise ValueError("Require the owned disposable PostgreSQL 18.6 database")


def _attest(connection, *, arm):
    probe.checked_schema(connection, ascii_reuse=arm == "ascii_reuse")


def _catalog_identity(connection):
    # prosrc is the only allowed pg_proc change. Compare both rows before/after
    # in the same source DB; physical restore can assign different OIDs.
    return (
        connection.exec_driver_sql(
            """SELECT to_jsonb(p) - 'prosrc' FROM pg_proc AS p
            JOIN pg_namespace AS n ON n.oid=p.pronamespace
            WHERE n.nspname='public'
              AND p.proname IN ('mineagent_rd_provider_graph',
                                'mineagent_rd_canonical_json')
            ORDER BY p.proname"""
        )
        .scalars()
        .all()
    )


def prepare_arm(engine, databases, arm):
    if arm not in _ARMS:
        raise ValueError("Unrecognized Qlib A/B arm")
    with engine.begin() as connection:
        _verify_database(connection, databases)
        _attest(connection, arm="baseline")
        if arm == "ascii_reuse":
            before = _catalog_identity(connection)
            if len(before) != 2:
                raise ValueError("Require exact function catalog inventory")
            connection.exec_driver_sql(
                probe.candidate_source().replace("@SCHEMA@", '"public"')
            )
            _attest(connection, arm=arm)
            if _catalog_identity(connection) != before:
                raise ValueError("Treatment changed more than the installed graph body")
    # Read committed installed versions separately; never attest a rollback.
    with engine.connect() as connection:
        _verify_database(connection, databases)
        _attest(connection, arm=arm)


def attest_restored(source_engine, restored_engine, databases, arm):
    if arm not in _ARMS:
        raise ValueError("Unrecognized Qlib A/B arm")
    with source_engine.connect() as source, restored_engine.connect() as restored:
        _verify_database(source, databases)
        _verify_database(restored, databases, restored=True)
        _attest(source, arm=arm)
        _attest(restored, arm=arm)
