"""Disposable PG18 proofs; component rows are not production workflow graphs."""

import copy
import json
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.app import rd_v3_invariant_probe_sql as probe
from tests.integration.test_row_validation_invariants import (
    call_outcome,
    catalog,
    execute_sql,
    pure_install_sql,
    row_corpus,
)

pytestmark = pytest.mark.postgres


def head(c):
    assert c.exec_driver_sql("SELECT version_num FROM alembic_version").all() == [
        (probe.PREDECESSOR,)
    ]


def test_rd_v3_current_head_catalog_roundtrip(migrated_engine):
    with migrated_engine.connect() as c, c.begin():
        head(c)
        before = catalog(c)
        execute_sql(c, probe.render_candidate_sql())
        after = catalog(c)
        assert {r[0] for r in before[0] if r not in after[0]} == set(probe.CHANGED)
        assert [r for r in before[0] if r[0] not in probe.CHANGED] == [
            r for r in after[0] if r[0] not in probe.CHANGED
        ]
        key = ("research_runs", probe.CONSTRAINT_NAME)
        assert [r for r in after[1] if r[:2] != key] == before[1]
        checks = [r for r in after[1] if r[:2] == key]
        assert len(checks) == 1 and checks[0][3] is True and checks[0][4] is False
        assert before[2:] == after[2:]
        execute_sql(c, "CREATE TABLE rd_v3_reference (LIKE @SCHEMA@.research_runs)")
        execute_sql(
            c,
            f"ALTER TABLE rd_v3_reference ADD CONSTRAINT expected CHECK ({probe.EXPRESSION})",
        )
        assert (
            c.exec_driver_sql(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='rd_v3_reference'::regclass AND conname='expected'"
            ).scalar_one()
            == checks[0][2]
        )
        c.exec_driver_sql("DROP TABLE rd_v3_reference")
        execute_sql(c, probe.render_candidate_sql("downgrade"))
        assert catalog(c) == before
        head(c)
        c.rollback()


@pytest.mark.parametrize("drift", ["helper", "immutable_body", "immutable_trigger"])
def test_rd_v3_preflight_refuses_drift_atomically(migrated_engine, drift):
    with migrated_engine.connect() as c, c.begin():
        head(c)
        if drift == "helper":
            source = probe.original("mineagent_rd_workflow_config")
            execute_sql(
                c, source.split("AS $$", 1)[0] + "AS $$ BEGIN RETURN true; END $$;"
            )
        elif drift == "immutable_body":
            c.exec_driver_sql(
                "CREATE OR REPLACE FUNCTION protect_research_inputs() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$;"
            )
        else:
            c.exec_driver_sql(
                "ALTER TRIGGER immutable_research_inputs ON research_runs RENAME TO drifted_run_inputs"
            )
        before = catalog(c)
        with pytest.raises(DBAPIError) as caught, c.begin_nested():
            execute_sql(c, probe.render_candidate_sql())
        assert caught.value.orig.sqlstate == "P0001"
        assert catalog(c) == before
        c.rollback()


def test_rd_v3_missing_row_and_null_fail_closed(migrated_engine):
    with migrated_engine.connect() as c, c.begin():
        head(c)
        cases = [
            (name, arg) for name in probe.CHANGED for arg in (None, str(UUID(int=7321)))
        ]
        before = {case: call_outcome(c, *case) for case in cases}
        execute_sql(c, probe.render_candidate_sql())
        assert {case: call_outcome(c, *case) for case in cases} == before
        c.rollback()


@pytest.fixture
def rd_v3_component(postgres_engine):
    # Frozen validators but synthetic table, not a full application graph.
    with postgres_engine.begin() as c:
        execute_sql(c, pure_install_sql())
        execute_sql(c, probe.original("mineagent_rd_schema"))
        c.exec_driver_sql(
            "CREATE TABLE rd_v3_rows(id integer PRIMARY KEY,purpose text NOT NULL,engine text NOT NULL,config jsonb NOT NULL)"
        )
    return postgres_engine


def add_check(c):
    execute_sql(
        c,
        f"ALTER TABLE rd_v3_rows ADD CONSTRAINT {probe.CONSTRAINT_NAME} CHECK ({probe.EXPRESSION})",
    )


def insert(c, identifier, config):
    c.execute(
        text(
            "INSERT INTO rd_v3_rows VALUES(:id,'factor_research','rdagent',CAST(:config AS jsonb))"
        ),
        {"id": identifier, "config": json.dumps(config)},
    )


def test_rd_v3_component_valid_invalid_and_v2_scope(rd_v3_component):
    rd, _, _ = row_corpus()
    valid = rd[1]
    assert valid["schema_version"] == "mineagent.factor-research.v3"
    invalid = copy.deepcopy(valid)
    invalid["task"] = None
    with rd_v3_component.begin() as c:
        add_check(c)
        assert (
            c.execute(
                text("SELECT mineagent_rd_workflow_config(CAST(:v AS jsonb))"),
                {"v": json.dumps(valid)},
            ).scalar_one()
            is True
        )
        insert(c, 1, valid)
        with pytest.raises(DBAPIError) as caught, c.begin_nested():
            insert(c, 2, invalid)
        assert caught.value.orig.sqlstate == "23514"
        assert caught.value.orig.diag.constraint_name == probe.CONSTRAINT_NAME
        with pytest.raises(DBAPIError), c.begin_nested():
            c.execute(
                text("UPDATE rd_v3_rows SET config=CAST(:v AS jsonb) WHERE id=1"),
                {"v": json.dumps(invalid)},
            )
        invalid_v2 = {"schema_version": "mineagent.factor-research.v2"}
        insert(c, 3, invalid_v2)
        assert (
            c.execute(
                text("SELECT mineagent_rd_provider_config(CAST(:v AS jsonb))"),
                {"v": json.dumps(invalid_v2)},
            ).scalar_one()
            is False
        )
        assert c.exec_driver_sql("SELECT id FROM rd_v3_rows ORDER BY id").all() == [
            (1,),
            (3,),
        ]


def test_rd_v3_component_historical_scan_is_atomic_not_old_validate(rd_v3_component):
    """Historical bad-row CHECK scan, not a production graph fixture."""
    with rd_v3_component.begin() as c:
        original = probe.original("mineagent_rd_workflow_config")
        execute_sql(
            c, original.split("AS $$", 1)[0] + "AS $$ BEGIN RETURN true; END $$;"
        )
        execute_sql(
            c,
            f"ALTER TABLE rd_v3_rows ADD CONSTRAINT stale_check CHECK ({probe.EXPRESSION})",
        )
        insert(c, 1, {"schema_version": "mineagent.factor-research.v3"})
        execute_sql(c, original)
        assert (
            c.exec_driver_sql(
                "SELECT convalidated FROM pg_constraint WHERE conrelid='rd_v3_rows'::regclass AND conname='stale_check'"
            ).scalar_one()
            is True
        )
        c.exec_driver_sql("ALTER TABLE rd_v3_rows VALIDATE CONSTRAINT stale_check")
        before = catalog(c)
        with pytest.raises(DBAPIError) as caught, c.begin_nested():
            add_check(c)
        assert caught.value.orig.sqlstate == "23514"
        assert caught.value.orig.diag.constraint_name == probe.CONSTRAINT_NAME
        assert catalog(c) == before
        assert c.exec_driver_sql("SELECT count(*) FROM rd_v3_rows").scalar_one() == 1
