"""Extended candidate PG probe; synthetic content is never native evidence.

Full schema = genuine migrations and all production triggers. Component tables
= exact pure CHECK mechanics only, with explicitly synthetic JSON fixtures and
no claims about SDK receipts, completed engine graphs, or deadline success.
"""

import hashlib
import json
import re
import time
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.app import row_validation_content_sql as m
from backend.app import row_validation_invariant_sql as base
from packages.factors.contracts import canonical_bytes
from script.sql_candidate_proof import export_component_measurements
from tests.integration.test_row_validation_invariants import (
    call_outcome,
    catalog,
    execute_sql,
    quoted_schema,
    rejection,
)
from tests.integration.test_row_validation_invariants import (
    component_db as component_db,  # noqa: PLC0414
)

pytestmark = pytest.mark.postgres


def assert_bodies(c, expected):
    for name in m.CHANGED:
        actual = c.execute(
            text("SELECT prosrc FROM pg_proc WHERE oid=to_regprocedure(:signature)"),
            {"signature": quoted_schema(c) + "." + name + "(uuid)"},
        ).scalar_one()
        assert actual == expected(name).split("AS $$", 1)[1].removesuffix(
            "$$;"
        ).replace("@SCHEMA@", quoted_schema(c))


def test_full_schema_minimal_and_extended_roundtrips_are_independently_selectable(
    migrated_engine,
):
    with migrated_engine.connect() as c, c.begin():
        before = catalog(c)
        execute_sql(c, base.render_candidate_sql())
        minimal = catalog(c)
        execute_sql(c, base.render_candidate_sql("downgrade"))
        assert catalog(c) == before
        execute_sql(c, m.render_candidate_sql())
        after = catalog(c)
        assert_bodies(c, m.candidate)
        changed = set(m.CHANGED)
        assert [r for r in before[0] if r[0] not in changed] == [
            r for r in after[0] if r[0] not in changed
        ]
        added = {(table, name) for table, name, _ in base.CONSTRAINTS + m.CONSTRAINTS}
        assert [r for r in after[1] if r[:2] not in added] == before[1]
        assert {r[:2] for r in after[1] if r[:2] in added} == added
        assert all(r[3] is True and r[4] is False for r in after[1] if r[:2] in added)
        assert after[2:] == before[2:] == minimal[2:]
        for table, name, expression in m.CONSTRAINTS:
            execute_sql(c, f"CREATE TABLE rvi_content_catalog (LIKE @SCHEMA@.{table});")
            execute_sql(
                c,
                f"ALTER TABLE rvi_content_catalog ADD CONSTRAINT {name} CHECK ({expression});",
            )
            expected = c.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='rvi_content_catalog'::regclass AND conname=:name"
                ),
                {"name": name},
            ).scalar_one()
            assert expected == next(r[2] for r in after[1] if r[:2] == (table, name))
            c.exec_driver_sql("DROP TABLE rvi_content_catalog")
        execute_sql(c, m.render_candidate_sql("downgrade"))
        assert_bodies(c, m.original)
        assert catalog(c) == before
        execute_sql(c, m.render_candidate_sql())
        assert catalog(c) == after
        assert c.exec_driver_sql("SELECT version_num FROM alembic_version").all() == [
            (base.PREDECESSOR,)
        ]
        c.rollback()


def test_full_schema_all_missing_roots_and_scope_preflight_preserved(migrated_engine):
    with migrated_engine.connect() as c, c.begin():
        args = [None, str(UUID(int=987654321))]
        before = {
            (name, arg): call_outcome(c, name, arg)
            for name in m.CHANGED
            for arg in args
        }
        execute_sql(c, m.render_candidate_sql())
        assert {
            (name, arg): call_outcome(c, name, arg)
            for name in m.CHANGED
            for arg in args
        } == before
        execute_sql(c, m.render_candidate_sql("downgrade"))
        table, name = next(iter(m.scope_foreign_keys().items()))
        with c.begin_nested() as point:
            c.exec_driver_sql(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
            state = catalog(c)
            with pytest.raises(DBAPIError) as caught, c.begin_nested():
                execute_sql(c, m.render_candidate_sql())
            assert "committed spec scope differs" in str(caught.value.orig)
            assert catalog(c) == state
            point.rollback()
        c.rollback()


@pytest.fixture
def content_db(component_db):
    with component_db.begin() as c:
        execute_sql(c, m.original("mineagent_structured_contract"))
        for table, columns in m.REQUIRED_COLUMNS.items():
            cols = ",".join(
                column
                + (" jsonb" if column == "strategy_input" else " text")
                + " NOT NULL"
                for column in columns
            )
            c.exec_driver_sql(
                f"CREATE TABLE rvi_content_{table}(id integer PRIMARY KEY,{cols})"
            )
    return component_db


def check_sql(constraints=m.CONSTRAINTS):
    return "\n".join(
        f"ALTER TABLE rvi_content_{table} ADD CONSTRAINT {name} CHECK ({expression});"
        for table, name, expression in constraints
    )


def content_corpus(index=0):
    from packages.openevolve_structured.objective import StructuredMeasurement
    from tests.openevolve.test_structured_candidate import setup_candidate
    from tests.structured.test_operator import declared_input
    from tests.structured.test_views import histories

    # Existing grammar/projection doubles only. Deliberately do not insert into
    # application tables or label this stopped engine/financial evidence.
    _, candidate = setup_candidate()
    candidate["program"]["threshold"] += index * 0.001
    inputs = declared_input().model_dump(mode="json")
    inputs["execution_id"] = str(UUID(int=1000 + index))
    result = histories(changed=False)[0].result.model_dump(mode="json")
    result["execution_id"] = inputs["execution_id"]
    measurement = StructuredMeasurement(
        execution_id=UUID(result["execution_id"]),
        structured_strategy_version_id=UUID(result["structured_strategy_version_id"]),
        snapshot_id=UUID(result["snapshot_id"]),
        strategy_input_sha256=result["strategy_input_sha256"],
        market_sha256=result["market_sha256"],
        result_sha256="a" * 64,
        objective_sha256="b" * 64,
        total_return_pct=None,
        max_drawdown_pct=None,
        closed_trade_count=0,
        eligible=False,
        combined_score=None,
        reason="undefined_accounting",
    ).model_dump(mode="json")

    def raw(value, key, digest):
        value = canonical_bytes(value)
        return {key: value.decode(), digest: hashlib.sha256(value).hexdigest()}

    return {
        "oe_structured_control_states": raw(
            {"component": "control", "index": index}, "state_canonical", "state_sha256"
        ),
        "oe_structured_candidates": raw(
            candidate, "candidate_canonical", "candidate_sha256"
        ),
        "oe_structured_executions": {
            "strategy_input": json.dumps(inputs),
            **raw(inputs, "strategy_input_canonical", "strategy_input_sha256"),
        },
        "oe_structured_engine_outcomes": {
            **raw(result, "result_canonical", "result_sha256"),
            **raw(measurement, "measurement_canonical", "measurement_sha256"),
        },
        # Raw-only CHECK intentionally does not pretend this is a valid epoch
        # bootstrap. Its exact relationally selected schema remains in graph.
        "oe_structured_epochs": raw(
            {"component": "raw epoch only", "index": index},
            "bootstrap_canonical",
            "bootstrap_sha256",
        ),
    }


def insert(c, table, values, index=0):
    cols = tuple(values)
    placeholders = ",".join(
        f"CAST(:{column} AS jsonb)" if column == "strategy_input" else ":" + column
        for column in cols
    )
    c.execute(
        text(
            f"INSERT INTO rvi_content_{table}(id,{','.join(cols)}) VALUES(:id,{placeholders})"
        ),
        {"id": index, **values},
    )


# Each mutation targets one newly moved predicate. Some also fail a separate
# commitment, which is expected; the individual stale test isolates each check.
BAD = (
    ("state_sha256", "0" * 64),
    ("candidate_canonical", "{}"),
    ("candidate_sha256", "0" * 64),
    ("strategy_input", "{}"),
    ("strategy_input_sha256", "0" * 64),
    ("result_canonical", "{}"),
    ("measurement_canonical", "{}"),
    ("result_sha256", "0" * 64),
    ("measurement_sha256", "0" * 64),
    ("bootstrap_sha256", "0" * 64),
)


def test_component_all_content_checks_valid_negative_and_savepoint(content_db):
    corpus = content_corpus()
    with content_db.begin() as c:
        execute_sql(c, check_sql())
        for table, values in corpus.items():
            insert(c, table, values)
        for (table, _, _), (field, value) in zip(m.CONSTRAINTS, BAD, strict=True):
            rejection(
                c,
                lambda table=table, field=field, value=value: insert(
                    c, table, {**corpus[table], field: value}, 1
                ),
            )
        for table, values in corpus.items():
            with c.begin_nested() as point:
                insert(c, table, values, 2)
                point.rollback()
            insert(c, table, values, 2)
            raw_column = next(k for k in values if k.endswith("_canonical"))
            for raw in ("null", "{", '{"x":1,"x":2}', ' {"x":1}', '{"x":1.0 }'):
                rejection(
                    c,
                    lambda table=table, values=values, raw_column=raw_column, raw=raw: (
                        insert(c, table, {**values, raw_column: raw}, 3)
                    ),
                )


@pytest.mark.parametrize("invariant", range(10))
def test_component_each_stale_content_check_fails_fresh_scan_atomically(
    content_db, invariant
):
    table, name, expression = m.CONSTRAINTS[invariant]
    helper = (
        "mineagent_structured_raw_commitment"
        if "raw_commitment" in expression
        else "mineagent_oes_contract"
        if "oes_contract" in expression
        else "mineagent_structured_contract"
    )
    original = m.original(helper) if helper in m.SOURCES else base.original(helper)
    permissive = original.split("AS $$", 1)[0] + "AS $$ BEGIN RETURN true; END $$;"
    values = content_corpus()[table]
    field, invalid = BAD[invariant]
    with content_db.begin() as c:
        execute_sql(c, permissive)
        target = "rvi_content_" + table
        execute_sql(
            c,
            f"ALTER TABLE {target} ADD CONSTRAINT stale_content CHECK ({expression});",
        )
        insert(c, table, {**values, field: invalid})
        execute_sql(c, original)
        assert (
            c.execute(
                text(
                    "SELECT convalidated FROM pg_constraint WHERE conrelid=CAST(:target AS regclass) AND conname='stale_content'"
                ),
                {"target": target},
            ).scalar_one()
            is True
        )
        c.exec_driver_sql(f"ALTER TABLE {target} VALIDATE CONSTRAINT stale_content")
        state = catalog(c)
        with pytest.raises(DBAPIError) as caught, c.begin_nested():
            execute_sql(
                c, f"ALTER TABLE {target} ADD CONSTRAINT {name} CHECK ({expression});"
            )
        assert caught.value.orig.sqlstate == "23514"
        assert caught.value.orig.diag.constraint_name == name
        assert catalog(c) == state


def test_component_epoch_counterexample_keeps_spec_selected_schema(content_db):
    from tests.openevolve.test_workflow_prompt import bootstrap

    value = bootstrap().model_dump(mode="json")
    value["runtime_profile_sha256"] = (
        "051b634c5de36dfb5ee2ec74a012ed8fa94a2c6ebb62ce743689e61bb4277d0f"
    )
    original = m.original("mineagent_oes_epoch_check")
    literal = (
        re.search(r"rule jsonb:='((?:''|[^'])*)'::jsonb", original)
        .group(1)
        .replace("''", "'")
    )
    branch = (
        next(
            line for line in original.splitlines() if " IF (CASE WHEN s.config" in line
        )
        .removeprefix(" IF ")
        .removesuffix(" IS NOT TRUE")
    )
    assert branch in m.candidate("mineagent_oes_epoch_check")
    with content_db.begin() as c:
        sql = f"""WITH s AS (SELECT CAST(:config AS jsonb) AS config), b AS
         (SELECT CAST(:bootstrap AS jsonb) AS j,CAST(:rule AS jsonb) AS rule)
         SELECT {branch}, @SCHEMA@.mineagent_oe_workflow_shape('bootstrap',j),
         (@SCHEMA@.mineagent_oe_workflow_shape('bootstrap',j) OR @SCHEMA@.mineagent_rd_schema(j,rule,coalesce(rule->'$defs','{{}}'::jsonb),0))
         FROM s CROSS JOIN b"""
        legacy, workflow, weak_union = c.execute(
            text(sql.replace("@SCHEMA@", quoted_schema(c))),
            {
                "config": json.dumps(
                    {"schema_version": "mineagent.oe-structured-provider-run.v1"}
                ),
                "bootstrap": json.dumps(value),
                "rule": literal,
            },
        ).one()
        assert legacy is False and workflow is True and weak_union is True


def test_component_extended_nonconstant_cost_and_write_overhead(content_db, capsys):
    # Independently attributable ten-predicate component. This does not combine
    # overlapping inclusive graph times or claim the8-second/native deadline.
    corpus = [content_corpus(i) for i in range(4)]
    samples = []
    with content_db.begin() as c:
        for phase in ("before", "after"):
            if phase == "after":
                execute_sql(c, check_sql())
            for sample in range(3):
                for table in m.REQUIRED_COLUMNS:
                    c.exec_driver_sql("TRUNCATE rvi_content_" + table)
                start = time.perf_counter()
                for i, rows in enumerate(corpus):
                    for table, values in rows.items():
                        insert(c, table, values, i)
                write_ms = 1000 * (time.perf_counter() - start)
                start = time.perf_counter()
                counts = {}
                for table in m.REQUIRED_COLUMNS:
                    expressions = [
                        expr for target, _, expr in m.CONSTRAINTS if target == table
                    ]
                    predicate = (
                        " AND ".join("(" + expr + ")" for expr in expressions)
                        if phase == "before"
                        else "true"
                    )
                    sql = f"SELECT count(*) FILTER(WHERE {predicate}),sum(t.id*17+i) FROM rvi_content_{table} t CROSS JOIN generate_series(1,8) i"
                    count, checksum = execute_sql(c, sql).one()
                    assert count == 32 and checksum == 960
                    counts[table] = count
                samples.append(
                    {
                        "phase": phase,
                        "sample": sample,
                        "write_ms": write_ms,
                        "read_ms": 1000 * (time.perf_counter() - start),
                        "rows_per_table": 4,
                        "checked_fetches": counts,
                    }
                )
    report = export_component_measurements("expanded", samples)
    with capsys.disabled():
        print(
            "RVI_EXTENDED_COMPONENT_ONLY "
            + json.dumps(report, sort_keys=True, allow_nan=False)
        )
