"""Real PostgreSQL trigger routing/locks, not a fabricated valid workflow graph.

The routing fixture installs the exact frozen031 reverse trigger function but
uses an explicit graph test double containing only the retained-outbox predicate
and a visit log. It proves event selection, OLD+NEW routing, deferral, rollback
and NOWAIT behavior. The separately migrated catalog test checks real installed
coverage. Neither fixture proves full workflow/runtime acceptance.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.app import oe_outbox_guard_sql as guard
from backend.app.oe_validation_cost_sql import original

pytestmark = pytest.mark.postgres


@pytest.fixture
def routing_db(postgres_engine):
    run, other, legacy, owner, event, extra = (uuid4() for _ in range(6))
    with postgres_engine.begin() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
        connection.exec_driver_sql(
            """CREATE TABLE research_runs (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL, config jsonb NOT NULL);
CREATE TABLE oe_structured_specs (run_id uuid PRIMARY KEY, owner_id uuid NOT NULL);
CREATE TABLE oe_workflow_submissions (run_id uuid NOT NULL);
CREATE TABLE outbox_events (
 id uuid PRIMARY KEY, run_id uuid NOT NULL REFERENCES research_runs(id),
 generation integer NOT NULL, created_at timestamptz DEFAULT now(),
 dispatched_at timestamptz, job_id integer, UNIQUE(run_id,generation));
CREATE TABLE graph_visits (ordinal bigint GENERATED ALWAYS AS IDENTITY, target uuid);
"""
        )
        # This is intentionally NOT the production graph checker. Its one
        # predicate is independently visible in the real attachment catalog test.
        connection.exec_driver_sql(
            f"""CREATE FUNCTION {schema}.mineagent_oes_graph_check(rid uuid)
RETURNS void LANGUAGE plpgsql SET search_path={schema},pg_catalog AS $$
BEGIN
 INSERT INTO graph_visits(target) VALUES(rid);
 IF EXISTS(SELECT 1 FROM research_runs WHERE id=rid
  AND config->>'schema_version'='mineagent.oe-structured-provider-run.v2')
 AND NOT EXISTS(SELECT 1 FROM outbox_events WHERE run_id=rid AND generation=1)
 THEN RAISE EXCEPTION 'outbox-graph-test-double: original evidence missing'; END IF;
END $$;"""
        )
        connection.exec_driver_sql(
            original(
                "031_workflow_structured_stage.py", "mineagent_oes_consistent"
            ).replace("@SCHEMA@", schema)
        )
        connection.exec_driver_sql(
            guard.render_upgrade_sql().replace("@SCHEMA@", schema)
        )
        for rid, version in (
            (run, "mineagent.oe-structured-provider-run.v2"),
            (other, "mineagent.oe-structured-provider-run.v2"),
            (legacy, "legacy"),
        ):
            connection.execute(
                text(
                    "INSERT INTO research_runs VALUES(:id,:owner,jsonb_build_object('schema_version',CAST(:version AS text)))"
                ),
                {"id": rid, "owner": owner, "version": version},
            )
            if rid != legacy:
                connection.execute(
                    text("INSERT INTO oe_structured_specs VALUES(:run,:owner)"),
                    {"run": rid, "owner": owner},
                )
            connection.execute(
                text(
                    "INSERT INTO outbox_events(id,run_id,generation) VALUES(:id,:run,:generation)"
                ),
                {
                    "id": event if rid == run else uuid4(),
                    "run": rid,
                    "generation": 9 if rid == legacy else 1,
                },
            )
        connection.execute(
            text("INSERT INTO outbox_events(id,run_id,generation) VALUES(:id,:run,2)"),
            {"id": extra, "run": run},
        )
    return SimpleNamespace(
        engine=postgres_engine,
        schema=schema,
        run=run,
        other=other,
        legacy=legacy,
        event=event,
        extra=extra,
    )


def force(connection):
    connection.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")


def visits(connection):
    return list(
        connection.scalars(text("SELECT target FROM graph_visits ORDER BY ordinal"))
    )


def test_installed_guard_catalog_and_original_outbox_dependency(migrated_engine):
    with migrated_engine.connect() as connection:
        rows = connection.execute(
            text("""SELECT t.tgname,t.tgdeferrable,t.tginitdeferred,p.proname,
 pg_get_triggerdef(t.oid) FROM pg_trigger t
 JOIN pg_proc p ON p.oid=t.tgfoid
 WHERE t.tgrelid='outbox_events'::regclass AND NOT t.tgisinternal""")
        ).all()
        found = {row[0]: row for row in rows}
        for name in guard.TRIGGERS:
            assert found[name][1:4] == (True, True, "mineagent_oes_consistent")
        assert "AFTER DELETE" in found[guard.TRIGGERS[0]][4]
        identity = found[guard.TRIGGERS[1]][4]
        assert "old.run_id IS DISTINCT FROM new.run_id" in identity
        assert "old.generation IS DISTINCT FROM new.generation" in identity
        assert {
            "rd_submission_outbox_closed",
            "rd_seed_closed",
            "rd_workflow_submission_graph",
        } <= found.keys()
        attachment = connection.scalar(
            text(
                "SELECT prosrc FROM pg_proc WHERE oid='mineagent_oe_workflow_attachment(uuid)'::regprocedure"
            )
        )
        assert (
            "NOT EXISTS(SELECT 1 FROM outbox_events WHERE run_id=q.run_id AND generation=1)"
            in attachment
        )
        assert connection.execute(
            text("""SELECT confupdtype,confdeltype FROM pg_constraint
 WHERE conrelid='outbox_events'::regclass AND confrelid='research_runs'::regclass
 AND contype='f'""")
        ).one() == ("a", "a")


@pytest.mark.parametrize("operation", ["delete", "generation", "retarget"])
def test_rejects_sole_generation_one_loss_in_graph_test_double(routing_db, operation):
    db = routing_db
    sql = {
        "delete": "DELETE FROM outbox_events WHERE id=:event",
        "generation": "UPDATE outbox_events SET generation=3 WHERE id=:event",
        "retarget": "UPDATE outbox_events SET run_id=:legacy WHERE id=:event",
    }[operation]
    with db.engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text(sql), {"event": db.event, "legacy": db.legacy})
            # Scheduling is deferred, not an early immutable-row prohibition.
            assert visits(connection) == []
            with pytest.raises(DBAPIError) as failure:
                force(connection)
            assert failure.value.orig.sqlstate == "P0001"
            assert "outbox-graph-test-double" in str(failure.value.orig)
        finally:
            transaction.rollback()
    with db.engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT generation FROM outbox_events WHERE id=:id"),
                {"id": db.event},
            )
            == 1
        )


def test_dispatch_metadata_and_identity_noop_do_not_recheck_graph(routing_db):
    db = routing_db
    with db.engine.begin() as holder:
        holder.execute(
            text("SELECT id FROM research_runs WHERE id=:id FOR UPDATE"), {"id": db.run}
        )
        with db.engine.begin() as delivery:
            delivery.execute(
                text(
                    "UPDATE outbox_events SET dispatched_at=now(),job_id=17,run_id=run_id,generation=generation WHERE id=:id"
                ),
                {"id": db.event},
            )
            force(delivery)
            assert visits(delivery) == []
            assert (
                delivery.scalar(
                    text("SELECT job_id FROM outbox_events WHERE id=:id"),
                    {"id": db.event},
                )
                == 17
            )


def test_old_and_new_distinct_runs_are_both_revalidated(routing_db):
    db = routing_db
    with db.engine.begin() as connection:
        connection.execute(
            text("UPDATE outbox_events SET run_id=:other WHERE id=:extra"),
            {"other": db.other, "extra": db.extra},
        )
        force(connection)
        assert visits(connection) == [db.run, db.other]


def test_retarget_checks_new_parent_nowait_after_old_target(routing_db):
    db = routing_db
    with db.engine.begin() as holder:
        # The FK's KEY SHARE can coexist with NO KEY UPDATE, but the existing
        # reverse check's FOR UPDATE NOWAIT cannot. This reaches the NEW side
        # without changing/deleting/deferring the original production-style FK.
        holder.execute(
            text("SELECT id FROM research_runs WHERE id=:id FOR NO KEY UPDATE"),
            {"id": db.other},
        )
        with db.engine.connect() as changing:
            transaction = changing.begin()
            try:
                changing.execute(
                    text("UPDATE outbox_events SET run_id=:other WHERE id=:extra"),
                    {"other": db.other, "extra": db.extra},
                )
                with pytest.raises(DBAPIError) as failure:
                    changing.exec_driver_sql(
                        "SET CONSTRAINTS oes032f_outbox_identity IMMEDIATE"
                    )
                assert failure.value.orig.sqlstate == "55P03"
            finally:
                transaction.rollback()


def test_concurrent_delete_cannot_commit_behind_locked_oe_parent(routing_db):
    db = routing_db
    with db.engine.begin() as holder:
        holder.execute(
            text("SELECT id FROM research_runs WHERE id=:id FOR UPDATE"), {"id": db.run}
        )
        with db.engine.connect() as deleting:
            transaction = deleting.begin()
            try:
                deleting.execute(
                    text("DELETE FROM outbox_events WHERE id=:id"), {"id": db.event}
                )
                with pytest.raises(DBAPIError) as failure:
                    force(deleting)
                assert failure.value.orig.sqlstate == "55P03"
            finally:
                transaction.rollback()
        assert (
            holder.scalar(
                text("SELECT count(*) FROM outbox_events WHERE id=:id"),
                {"id": db.event},
            )
            == 1
        )


def test_original_foreign_key_still_blocks_parent_deletion(routing_db):
    db = routing_db
    with db.engine.connect() as connection:
        transaction = connection.begin()
        try:
            with pytest.raises(DBAPIError) as failure:
                connection.execute(
                    text("DELETE FROM research_runs WHERE id=:id"), {"id": db.run}
                )
            assert failure.value.orig.sqlstate == "23503"
        finally:
            transaction.rollback()


def test_deferred_replacement_preserves_required_evidence(routing_db):
    db = routing_db
    with db.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM outbox_events WHERE id=:id"), {"id": db.event}
        )
        connection.execute(
            text("INSERT INTO outbox_events(id,run_id,generation) VALUES(:id,:run,1)"),
            {"id": uuid4(), "run": db.run},
        )
        force(connection)
        assert visits(connection) == [db.run]


def test_test_only_cascading_delete_still_schedules_guard(routing_db):
    db = routing_db
    parent = uuid4()
    # Production outbox->run has NO ACTION, verified above. This extra test-only
    # relationship exercises PostgreSQL's cascaded row-delete trigger behavior.
    with db.engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE cascade_probe(id uuid PRIMARY KEY); ALTER TABLE outbox_events ADD COLUMN cascade_probe_id uuid REFERENCES cascade_probe(id) ON DELETE CASCADE"
        )
        connection.execute(
            text("INSERT INTO cascade_probe VALUES(:id)"), {"id": parent}
        )
        connection.execute(
            text("UPDATE outbox_events SET cascade_probe_id=:parent WHERE id=:event"),
            {"parent": parent, "event": db.event},
        )
    with db.engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                text("DELETE FROM cascade_probe WHERE id=:id"), {"id": parent}
            )
            with pytest.raises(DBAPIError) as failure:
                force(connection)
            assert failure.value.orig.sqlstate == "P0001"
            assert "outbox-graph-test-double" in str(failure.value.orig)
        finally:
            transaction.rollback()


def test_unrelated_legacy_outbox_keeps_previous_behavior(routing_db):
    db = routing_db
    with db.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM outbox_events WHERE run_id=:id"), {"id": db.legacy}
        )
        force(connection)
        assert visits(connection) == []


def test_graph_mutation_retains_read_committed_requirement(routing_db):
    db = routing_db
    with db.engine.connect().execution_options(
        isolation_level="REPEATABLE READ"
    ) as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                text("DELETE FROM outbox_events WHERE id=:id"), {"id": db.event}
            )
            with pytest.raises(DBAPIError) as failure:
                force(connection)
            assert failure.value.orig.sqlstate == "P0001"
            assert "READ COMMITTED required" in str(failure.value.orig)
        finally:
            transaction.rollback()


@pytest.mark.parametrize(
    "historical", ["pending", "delivered", "missing", "retargeted", "wrong_generation"]
)
def test_upgrade_audits_populated_original_outbox_evidence(postgres_engine, historical):
    """Only the exact pre-existing attachment/outbox dependency is modeled here.

    This fixture has no runtime graph. It tests the unmodified audit SQL against
    stored rows before any of the new triggers exist, not workflow acceptance.
    """
    run, other = uuid4(), uuid4()
    with postgres_engine.begin() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
        connection.exec_driver_sql("""CREATE TABLE oe_workflow_submissions(run_id uuid NOT NULL);
CREATE TABLE outbox_events(run_id uuid NOT NULL,generation integer NOT NULL,
 dispatched_at timestamptz,job_id integer);""")
        # Needed only as the DDL trigger target; the audit does not call it.
        connection.exec_driver_sql(
            f"CREATE FUNCTION {schema}.mineagent_oes_consistent() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'audit fixture trigger must not execute'; END $$"
        )
        connection.execute(
            text("INSERT INTO oe_workflow_submissions VALUES(:run)"), {"run": run}
        )
        if historical != "missing":
            connection.execute(
                text(
                    "INSERT INTO outbox_events VALUES(:run,:generation,CASE WHEN :delivered THEN now() END,CASE WHEN :delivered THEN 17 END)"
                ),
                {
                    "run": other if historical == "retargeted" else run,
                    "generation": 2 if historical == "wrong_generation" else 1,
                    "delivered": historical == "delivered",
                },
            )
    with postgres_engine.begin() as connection:
        upgrade = guard.render_upgrade_sql().replace("@SCHEMA@", schema)
        savepoint = connection.begin_nested()
        if historical in {"pending", "delivered"}:
            connection.exec_driver_sql(upgrade)
            savepoint.commit()
            expected = set(guard.TRIGGERS)
        else:
            with pytest.raises(DBAPIError) as failure:
                connection.exec_driver_sql(upgrade)
            assert failure.value.orig.sqlstate == "P0001"
            assert "retained generation-one outbox evidence missing" in str(
                failure.value.orig
            )
            savepoint.rollback()
            expected = set()
        names = set(
            connection.scalars(
                text(
                    "SELECT tgname FROM pg_trigger WHERE tgrelid='outbox_events'::regclass AND NOT tgisinternal"
                )
            )
        )
        assert names == expected
