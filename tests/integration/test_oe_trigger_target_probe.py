"""Isolated trigger-dispatch proof, NOT whole-graph or native acceptance.

The catalog tests use the installed schema and preserve its production triggers.
The dispatch tests use an explicitly synthetic schema and an instrumented graph
checker. They prove event-local target routing, rollback and NOWAIT behavior;
they do not prove concurrent closure of the real graph or provider deadlines.
"""

from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.app import oe_trigger_target_probe_sql as probe
from tests.integration.test_row_validation_invariants import catalog, execute_sql

pytestmark = pytest.mark.postgres


def uid(value):
    return UUID(int=value)


def test_installed_trigger_catalog_roundtrip(migrated_engine):
    with migrated_engine.connect() as c, c.begin():
        before = catalog(c)
        execute_sql(c, probe.render_candidate_sql())
        after = catalog(c)
        name = "mineagent_oes_consistent"
        assert [row for row in before[0] if row[0] != name] == [
            row for row in after[0] if row[0] != name
        ]
        assert before[1:] == after[1:]
        assert [row for row in before[0] if row[0] == name] != [
            row for row in after[0] if row[0] == name
        ]
        execute_sql(c, probe.render_candidate_sql("downgrade"))
        assert catalog(c) == before


def test_installed_trigger_repeat_install_fails_atomically(migrated_engine):
    with migrated_engine.connect() as c, c.begin():
        execute_sql(c, probe.render_candidate_sql())
        before = catalog(c)
        with pytest.raises(DBAPIError), c.begin_nested():
            execute_sql(c, probe.render_candidate_sql())
        assert catalog(c) == before
        execute_sql(c, probe.render_candidate_sql("downgrade"))


@pytest.mark.parametrize("blocker", ["advisory", "table", "drift", "isolation"])
def test_installed_trigger_preflight_is_atomic(migrated_engine, blocker):
    with migrated_engine.connect() as held, held.begin():
        if blocker == "advisory":
            held.exec_driver_sql("SELECT pg_advisory_xact_lock(1413960260,1129270340)")
        elif blocker == "table":
            held.exec_driver_sql("LOCK TABLE research_runs IN ROW EXCLUSIVE MODE")
        with migrated_engine.connect() as c:
            if blocker == "isolation":
                c = c.execution_options(isolation_level="REPEATABLE READ")
            with c.begin():
                if blocker == "drift":
                    execute_sql(
                        c,
                        probe.original().replace(
                            "BEGIN\n", "BEGIN\n -- unreviewed drift\n", 1
                        ),
                    )
                before = catalog(c)
                with pytest.raises(DBAPIError) as caught, c.begin_nested():
                    execute_sql(c, probe.render_candidate_sql())
                assert caught.value.orig.sqlstate == (
                    "55P03" if blocker == "table" else "P0001"
                )
                assert catalog(c) == before
                c.rollback()


@pytest.fixture(params=["baseline", "candidate", "restored"])
def dispatch_db(postgres_engine, request):
    """No production table or validator is replaced by this synthetic harness."""
    with postgres_engine.begin() as c:
        execute_sql(
            c,
            """
        CREATE TABLE research_runs(id uuid PRIMARY KEY, config jsonb NOT NULL);
        CREATE TABLE oe_structured_specs(run_id uuid PRIMARY KEY, owner_id uuid NOT NULL);
        CREATE TABLE event_rows(id integer PRIMARY KEY, run_id uuid, owner_id uuid, touch integer DEFAULT 0);
        CREATE TABLE owner_rows(id integer PRIMARY KEY, owner_id uuid, touch integer DEFAULT 0);
        CREATE TABLE graph_calls(target uuid NOT NULL);
        CREATE TABLE invalid_graphs(target uuid PRIMARY KEY);
        CREATE FUNCTION @SCHEMA@.mineagent_oes_graph_check(target uuid) RETURNS void
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
          IF EXISTS(SELECT 1 FROM @SCHEMA@.invalid_graphs g WHERE g.target=$1)
          THEN RAISE EXCEPTION 'Synthetic graph rejection'; END IF;
          INSERT INTO @SCHEMA@.graph_calls VALUES($1);
        END $$;
        """,
        )
        for value, owner in ((1, 101), (2, 101), (3, 102)):
            c.execute(
                text("INSERT INTO research_runs VALUES(:r, '{}'::jsonb)"),
                {"r": uid(value)},
            )
            c.execute(
                text("INSERT INTO oe_structured_specs VALUES(:r,:o)"),
                {"r": uid(value), "o": uid(owner)},
            )
        c.execute(
            text("INSERT INTO event_rows(id,run_id,owner_id) VALUES(1,:r,:o)"),
            {"r": uid(1), "o": uid(101)},
        )
        c.execute(
            text("INSERT INTO owner_rows(id,owner_id) VALUES(1,:o)"), {"o": uid(101)}
        )
        execute_sql(c, probe.original())
        if request.param in {"candidate", "restored"}:
            execute_sql(c, probe.candidate())
        if request.param == "restored":
            execute_sql(c, probe.original())
        for table in ("event_rows", "owner_rows"):
            execute_sql(
                c,
                f"""CREATE CONSTRAINT TRIGGER dispatch_check
            AFTER INSERT OR UPDATE OR DELETE ON {table}
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            EXECUTE FUNCTION @SCHEMA@.mineagent_oes_consistent()""",
            )
    return postgres_engine, request.param


def counts(c):
    return dict(
        c.exec_driver_sql(
            "SELECT target,count(*) FROM graph_calls GROUP BY target"
        ).all()
    )


def flush(c):
    c.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
    c.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED")


def test_dispatch_duplicate_targets_and_no_transaction_cache(dispatch_db):
    engine, phase = dispatch_db
    per_event = 1 if phase == "candidate" else 2
    with engine.begin() as c:
        for event in range(1, 3):
            c.exec_driver_sql("UPDATE event_rows SET touch=touch+1 WHERE id=1")
            flush(c)
            assert counts(c) == {uid(1): per_event * event}
        c.exec_driver_sql("TRUNCATE graph_calls")
        c.exec_driver_sql("UPDATE owner_rows SET touch=touch+1 WHERE id=1")
        flush(c)
        assert counts(c) == {uid(1): per_event, uid(2): per_event}


def test_dispatch_retains_both_old_and_new_direct_and_reverse_targets(dispatch_db):
    engine, _ = dispatch_db
    with engine.begin() as c:
        c.execute(text("UPDATE event_rows SET run_id=:r WHERE id=1"), {"r": uid(3)})
        flush(c)
        assert counts(c) == {uid(1): 1, uid(3): 1}
        c.exec_driver_sql("TRUNCATE graph_calls")
        c.execute(text("UPDATE owner_rows SET owner_id=:o WHERE id=1"), {"o": uid(102)})
        flush(c)
        assert counts(c) == {uid(1): 1, uid(2): 1, uid(3): 1}


def test_dispatch_insert_delete_and_missing_target(dispatch_db):
    engine, _ = dispatch_db
    with engine.begin() as c:
        c.execute(text("INSERT INTO event_rows(id,run_id) VALUES(2,:r)"), {"r": uid(2)})
        flush(c)
        c.exec_driver_sql("DELETE FROM event_rows WHERE id=2")
        flush(c)
        assert counts(c) == {uid(2): 2}
        c.execute(
            text("INSERT INTO event_rows(id,run_id) VALUES(3,:r)"), {"r": uid(99)}
        )
        flush(c)
        assert counts(c) == {uid(2): 2}


def test_dispatch_rejection_rolls_back_event_and_observations(dispatch_db):
    engine, _ = dispatch_db
    with engine.begin() as c:
        c.execute(text("INSERT INTO invalid_graphs VALUES(:r)"), {"r": uid(3)})
        with (
            pytest.raises(DBAPIError, match="Synthetic graph rejection"),
            c.begin_nested(),
        ):
            c.execute(text("UPDATE event_rows SET run_id=:r WHERE id=1"), {"r": uid(3)})
            flush(c)
        assert c.exec_driver_sql(
            "SELECT run_id FROM event_rows WHERE id=1"
        ).scalar_one() == uid(1)
        assert counts(c) == {}


def test_dispatch_two_connections_preserve_nowait_lock(dispatch_db):
    engine, _ = dispatch_db
    with engine.connect() as held, held.begin():
        held.exec_driver_sql("SET LOCAL lock_timeout='2s'")
        held.execute(
            text("SELECT id FROM research_runs WHERE id=:r FOR UPDATE"), {"r": uid(1)}
        )
        with engine.connect() as contender, contender.begin():
            contender.exec_driver_sql("SET LOCAL lock_timeout='2s'")
            with pytest.raises(DBAPIError) as caught, contender.begin_nested():
                contender.exec_driver_sql(
                    "UPDATE event_rows SET touch=touch+1 WHERE id=1"
                )
                flush(contender)
            assert caught.value.orig.sqlstate == "55P03"
            assert (
                contender.exec_driver_sql(
                    "SELECT touch FROM event_rows WHERE id=1"
                ).scalar_one()
                == 0
            )
            assert counts(contender) == {}


def test_dispatch_non_read_committed_rejected(dispatch_db):
    engine, _ = dispatch_db
    with (
        engine.connect().execution_options(isolation_level="REPEATABLE READ") as c,
        c.begin(),
    ):
        with (
            pytest.raises(DBAPIError, match="READ COMMITTED required"),
            c.begin_nested(),
        ):
            c.exec_driver_sql("UPDATE event_rows SET touch=touch+1 WHERE id=1")
            flush(c)
        assert counts(c) == {}


def test_dispatch_reverse_bound_not_deduplicated_away(dispatch_db):
    engine, _ = dispatch_db
    with engine.begin() as c:
        c.execute(
            text("""INSERT INTO oe_structured_specs(run_id,owner_id)
          SELECT (lpad(to_hex(n),32,'0'))::uuid,:o FROM generate_series(1000,11000) n"""),
            {"o": uid(103)},
        )
        with (
            pytest.raises(DBAPIError, match="Structured reverse graph bound"),
            c.begin_nested(),
        ):
            c.execute(
                text("UPDATE owner_rows SET owner_id=:o WHERE id=1"), {"o": uid(103)}
            )
            flush(c)
        assert counts(c) == {}
