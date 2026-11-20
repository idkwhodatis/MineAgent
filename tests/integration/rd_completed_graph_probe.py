"""Actual terminal provider-v2 parent/seeded-child baseline, never a candidate.

Called only after the original native028 browser/provider assertions succeed.
Upgrade that owned, quiescent database to032g, retain all installed guards, and
roll back every timestamp/adversarial write. This proves neither workflow-v3,
active heartbeat concurrency, OE, nor deadline improvement. No SDK is called.
"""

import json
import re
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

PROOF_PATH = Path("var/numeric-proof/rd-completed-v2-proof.json")
PROOF = {
    "schema": "mineagent.completed-rd-v2-baseline.v1",
    "revision": "032g_schema_keyset_cost",
    "completed_runs": 2,
    "timestamp_rollback_cases": 2,
    "identity_rejection_cases": 2,
    "nowait_contention_cases": 4,
    "candidate_activated": False,
}


def _snapshot(engine):
    """Whole owned schema row fingerprints and catalog stay private in memory."""
    from workflow_keyset_upgrade import catalog

    with engine.connect() as c, c.begin():
        c.exec_driver_sql("SET TRANSACTION READ ONLY")
        tables = (
            c.exec_driver_sql("""SELECT r.relname FROM pg_class r
          JOIN pg_namespace n ON n.oid=r.relnamespace
          WHERE n.nspname='public' AND r.relkind='r' ORDER BY r.relname""")
            .scalars()
            .all()
        )
        assert 0 < len(tables) <= 256
        fingerprints = {}
        for table in tables:
            assert re.fullmatch(r"[a-z][a-z0-9_]{0,62}", table)
            count, digest = c.exec_driver_sql(f"""SELECT count(*),
              encode(sha256(convert_to(coalesce(jsonb_agg(value ORDER BY value::text)::text,'[]'),'UTF8')),'hex')
              FROM (SELECT to_jsonb(t) AS value FROM public."{table}" t LIMIT 16385) q""").one()
            assert 0 <= count <= 16384
            assert re.fullmatch(r"[0-9a-f]{64}", digest)
            fingerprints[table] = (count, digest)
        return catalog(c), fingerprints


def _require_unchanged(engine, before):
    if _snapshot(engine) != before:
        raise AssertionError("Completed RD probe changed retained schema or rows")


def _attempt_scope_constraints(c):
    rows = c.exec_driver_sql("""SELECT n.nspname,r.relname,f.conname
      FROM pg_constraint f JOIN pg_class r ON r.oid=f.conrelid
      JOIN pg_namespace n ON n.oid=r.relnamespace
      WHERE f.contype='f' AND f.confrelid='public.execution_attempts'::regclass
      AND f.convalidated AND NOT f.condeferrable AND n.nspname='public'
      AND ARRAY(SELECT a.attname::text FROM unnest(f.conkey) WITH ORDINALITY k(num,ord)
        JOIN pg_attribute a ON a.attrelid=f.conrelid AND a.attnum=k.num ORDER BY k.ord)
        =ARRAY['run_id','generation','attempt_id']
      AND ARRAY(SELECT a.attname::text FROM unnest(f.confkey) WITH ORDINALITY k(num,ord)
        JOIN pg_attribute a ON a.attrelid=f.confrelid AND a.attnum=k.num ORDER BY k.ord)
        =ARRAY['run_id','generation','id']""").all()
    assert rows, "Actual retained attempt-scope foreign keys are required"
    return set(rows)


def _lock_run_attempt(c, run_id, attempt_id):
    c.execute(
        text("SELECT id FROM research_runs WHERE id=:id FOR UPDATE NOWAIT"),
        {"id": run_id},
    ).scalar_one()
    c.execute(
        text("SELECT id FROM execution_attempts WHERE id=:id FOR UPDATE NOWAIT"),
        {"id": attempt_id},
    ).scalar_one()


def _validate_completed(c, run_id):
    run = c.execute(
        text("""SELECT status, config->>'schema_version'
      FROM research_runs WHERE id=:id"""),
        {"id": run_id},
    ).one()
    assert run == ("succeeded", "mineagent.factor-research.v2")
    attempt = c.execute(
        text("""SELECT id,status,sandbox_stopped,finished_at
      FROM execution_attempts WHERE run_id=:id"""),
        {"id": run_id},
    ).one()
    assert attempt.status == "succeeded" and attempt.sandbox_stopped is True
    assert attempt.finished_at is not None
    units = c.execute(
        text("""SELECT a.role,s.status FROM research_process_admissions a
      JOIN research_process_states s ON s.admission_id=a.id WHERE a.run_id=:id
      ORDER BY a.role"""),
        {"id": run_id},
    ).all()
    assert units == [
        (role, "stopped")
        for role in (
            "factor_cutoff_candidate",
            "factor_normalizer",
            "native_evaluator",
            "rd_orchestrator",
        )
    ]
    transports = (
        c.execute(
            text("SELECT state FROM research_model_transports WHERE run_id=:id"),
            {"id": run_id},
        )
        .scalars()
        .all()
    )
    assert 4 < len(transports) <= 32 and set(transports) == {"completed"}
    receipts = c.execute(
        text("SELECT count(*) FROM rd_provider_transport_receipts WHERE run_id=:id"),
        {"id": run_id},
    ).scalar_one()
    assert receipts == 3 * len(transports)
    c.execute(
        text("SELECT public.mineagent_rd_provider_graph(:id)"), {"id": run_id}
    ).scalar_one()
    # Exact original source validator must accept each retained real source.
    violations = (
        c.execute(
            text("""SELECT public.mineagent_rd_source_violation(id)
      FROM factor_generation_sources WHERE run_id=:id"""),
            {"id": run_id},
        )
        .scalars()
        .all()
    )
    assert violations == [None]
    return attempt.id


def prove_completed_rd_graph(engine, parent_run_id, child_run_id):
    import test_backup_restore as legacy

    assert parent_run_id != child_run_id
    assert not PROOF_PATH.exists(), "A fresh completed-RD proof is required"
    with engine.connect() as c:
        assert c.exec_driver_sql("SELECT version_num FROM alembic_version").all() == [
            ("028_oe_structured_search",)
        ]
    legacy._upgrade(engine, "032g_schema_keyset_cost")
    with engine.connect() as c, c.begin():
        c.exec_driver_sql("SET TRANSACTION READ ONLY")
        assert c.exec_driver_sql("SELECT version_num FROM alembic_version").all() == [
            ("032g_schema_keyset_cost",)
        ]
        attempts = [
            (run_id, _validate_completed(c, run_id))
            for run_id in (parent_run_id, child_run_id)
        ]
        scope_constraints = _attempt_scope_constraints(c)
    before = _snapshot(engine)
    for run_id, attempt_id in attempts:
        with engine.connect() as c:
            transaction = c.begin()
            try:
                _lock_run_attempt(c, run_id, attempt_id)
                c.execute(
                    text("""UPDATE execution_attempts SET
                  heartbeat_at=heartbeat_at+interval '1 microsecond',
                  lease_expires_at=lease_expires_at+interval '1 microsecond'
                  WHERE id=:id"""),
                    {"id": attempt_id},
                )
                c.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
                assert _validate_completed(c, run_id) == attempt_id
            finally:
                transaction.rollback()
        _require_unchanged(engine, before)
        with engine.connect() as c:
            transaction = c.begin()
            try:
                _lock_run_attempt(c, run_id, attempt_id)
                # Existing retained admission/operation scope FKs reject this;
                # no status-only rejection is assumed for a terminal attempt.
                with pytest.raises(DBAPIError) as caught, c.begin_nested():
                    c.execute(
                        text("""UPDATE execution_attempts SET
                      heartbeat_at=heartbeat_at+interval '1 microsecond',
                      generation=generation+1 WHERE id=:id"""),
                        {"id": attempt_id},
                    )
                    c.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
                assert caught.value.orig.sqlstate == "23503"
                detail = caught.value.orig.diag
                assert (
                    detail.schema_name,
                    detail.table_name,
                    detail.constraint_name,
                ) in scope_constraints
            finally:
                transaction.rollback()
        _require_unchanged(engine, before)
        for direct_attempt_write in (False, True):
            with engine.connect() as held, held.begin():
                held.exec_driver_sql("SET LOCAL lock_timeout='2s'")
                held.execute(
                    text("SELECT id FROM research_runs WHERE id=:id FOR UPDATE NOWAIT"),
                    {"id": run_id},
                ).scalar_one()
                if not direct_attempt_write:
                    held.execute(
                        text(
                            "SELECT id FROM execution_attempts WHERE id=:id FOR UPDATE NOWAIT"
                        ),
                        {"id": attempt_id},
                    ).scalar_one()
                with engine.connect() as contender:
                    transaction = contender.begin()
                    try:
                        contender.exec_driver_sql("SET LOCAL lock_timeout='2s'")
                        with (
                            pytest.raises(DBAPIError) as caught,
                            contender.begin_nested(),
                        ):
                            if direct_attempt_write:
                                # Run-only holder permits tuple access, so the
                                # installed BEFORE run-lock trigger must reject.
                                contender.execute(
                                    text("""UPDATE execution_attempts SET
                                  heartbeat_at=heartbeat_at+interval '1 microsecond'
                                  WHERE id=:id"""),
                                    {"id": attempt_id},
                                )
                            else:
                                _lock_run_attempt(contender, run_id, attempt_id)
                        assert caught.value.orig.sqlstate == "55P03"
                    finally:
                        transaction.rollback()
            _require_unchanged(engine, before)
    with engine.connect() as c, c.begin():
        c.exec_driver_sql("SET TRANSACTION READ ONLY")
        for run_id, attempt_id in attempts:
            assert _validate_completed(c, run_id) == attempt_id
    _require_unchanged(engine, before)
    PROOF_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROOF_PATH.write_text(json.dumps(PROOF, sort_keys=True) + "\n")
