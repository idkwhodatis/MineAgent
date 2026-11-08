"""Opt-in candidate probe, not a migration or native-runtime acceptance test.

Full-schema tests retain every production trigger. Component tables below are
explicitly synthetic row-local fixtures: they do not prove provider receipts,
whole graph validity, concurrency closure, or the120s/8s runtime deadlines.
No test installs PostgreSQL, disables a production trigger, or advances head.
"""

import copy
import hashlib
import json
import re
import time
from uuid import UUID

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.app import row_validation_invariant_sql as m
from packages.factors.contracts import canonical_bytes
from script.sql_candidate_proof import export_component_measurements

pytestmark = pytest.mark.postgres


def quoted_schema(connection):
    value = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
    assert re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value)
    return '"' + value + '"'


def execute_sql(connection, sql):
    return connection.exec_driver_sql(
        sql.replace("@SCHEMA@", quoted_schema(connection)),
        execution_options={"no_parameters": True},
    )


def catalog(connection):
    # Include ACL, trigger enablement, nullability and constraint validation,
    # not merely function text. A no-op downgrade must return all these exactly.
    return tuple(
        connection.exec_driver_sql(query).all()
        for query in (
            """SELECT p.proname,pg_get_function_identity_arguments(p.oid),
             pg_get_functiondef(p.oid),p.proacl::text FROM pg_proc p
             JOIN pg_namespace n ON n.oid=p.pronamespace
             WHERE n.nspname=current_schema() AND p.prokind='f' ORDER BY 1,2""",
            """SELECT r.relname,c.conname,pg_get_constraintdef(c.oid),
             c.convalidated,c.condeferrable FROM pg_constraint c
             JOIN pg_class r ON r.oid=c.conrelid JOIN pg_namespace n ON n.oid=r.relnamespace
             WHERE n.nspname=current_schema() ORDER BY 1,2""",
            """SELECT r.relname,t.tgname,pg_get_triggerdef(t.oid),t.tgenabled
             FROM pg_trigger t JOIN pg_class r ON r.oid=t.tgrelid
             JOIN pg_namespace n ON n.oid=r.relnamespace
             WHERE n.nspname=current_schema() AND NOT t.tgisinternal ORDER BY 1,2""",
            """SELECT r.relname,a.attname,a.atttypid,a.attnotnull
             FROM pg_attribute a JOIN pg_class r ON r.oid=a.attrelid
             JOIN pg_namespace n ON n.oid=r.relnamespace
             WHERE n.nspname=current_schema() AND r.relkind='r' AND a.attnum>0
             AND NOT a.attisdropped ORDER BY 1,2""",
        )
    )


def bodies(connection, expected):
    for name in m.CHANGED:
        actual = connection.execute(
            text("SELECT prosrc FROM pg_proc WHERE oid=to_regprocedure(:signature)"),
            {"signature": quoted_schema(connection) + "." + name + "(uuid)"},
        ).scalar_one()
        source = expected(name).split("AS $$", 1)[1].removesuffix("$$;")
        assert actual == source.replace("@SCHEMA@", quoted_schema(connection))


def test_full_schema_catalog_roundtrip_and_exact_new_checks(migrated_engine):
    with migrated_engine.connect() as c, c.begin():
        before = catalog(c)
        bodies(c, m.original)
        execute_sql(c, m.render_candidate_sql())
        after = catalog(c)
        bodies(c, m.candidate)
        assert [row for row in before[0] if row[0] not in m.CHANGED] == [
            row for row in after[0] if row[0] not in m.CHANGED
        ]
        added = {(table, name) for table, name, _ in m.CONSTRAINTS}
        assert [row for row in after[1] if row[:2] not in added] == before[1]
        new_checks = [row for row in after[1] if row[:2] in added]
        assert {row[:2] for row in new_checks} == added
        assert all(row[3] is True and row[4] is False for row in new_checks)
        assert after[2:] == before[2:]
        # PostgreSQL's own deparser supplies exact normalization. The reference
        # relation exists only during this catalog comparison; it is not a
        # runtime helper or a graph fixture, and no existing triggers change.
        for table, name, expression in m.CONSTRAINTS:
            execute_sql(
                c, f"CREATE TABLE rvi_catalog_reference (LIKE @SCHEMA@.{table});"
            )
            execute_sql(
                c,
                f"ALTER TABLE rvi_catalog_reference ADD CONSTRAINT {name} CHECK ({expression});",
            )
            definition = c.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='rvi_catalog_reference'::regclass AND conname=:name"
                ),
                {"name": name},
            ).scalar_one()
            assert definition == next(
                row[2] for row in new_checks if row[:2] == (table, name)
            )
            c.exec_driver_sql("DROP TABLE rvi_catalog_reference")
        execute_sql(c, m.render_candidate_sql("downgrade"))
        bodies(c, m.original)
        assert catalog(c) == before
        execute_sql(c, m.render_candidate_sql())
        assert catalog(c) == after
        assert c.exec_driver_sql("SELECT version_num FROM alembic_version").all() == [
            (m.PREDECESSOR,)
        ]
        # Caller rollback below prevents this isolated candidate from escaping.
        c.rollback()


def call_outcome(c, name, argument):
    try:
        with c.begin_nested():
            c.execute(text(f"SELECT {name}(CAST(:value AS uuid))"), {"value": argument})
        return "ok", None
    except DBAPIError as error:
        return error.orig.sqlstate, error.orig.diag.message_primary


def test_full_schema_missing_row_and_null_uuid_behavior_is_identical(migrated_engine):
    with migrated_engine.connect() as c, c.begin():
        args = [None, str(UUID(int=987654321))]
        old = {
            (name, arg): call_outcome(c, name, arg)
            for name in m.CHANGED
            for arg in args
        }
        execute_sql(c, m.render_candidate_sql())
        assert {
            (name, arg): call_outcome(c, name, arg)
            for name in m.CHANGED
            for arg in args
        } == old
        assert old["mineagent_rd_provider_graph", args[1]][0] == "ok"
        assert old["mineagent_rd_provider_queued_config", args[1]][0] == "P0002"
        assert old["mineagent_oes_spec_check", args[1]][0] == "P0001"
        c.rollback()


@pytest.mark.parametrize(
    "blocker", ["isolation", "advisory", "table", "helper", "default"]
)
def test_full_schema_preflight_failure_is_atomic(migrated_engine, blocker):
    with migrated_engine.connect() as held, held.begin():
        if blocker == "advisory":
            held.exec_driver_sql("SELECT pg_advisory_xact_lock(1413960260,1129270340)")
        elif blocker == "table":
            held.exec_driver_sql("LOCK TABLE research_runs IN ROW EXCLUSIVE MODE")
        with migrated_engine.connect() as c:
            if blocker == "isolation":
                c = c.execution_options(isolation_level="REPEATABLE READ")
            with c.begin():
                if blocker == "helper":
                    execute_sql(
                        c,
                        m.original("mineagent_rd_provider_config").replace(
                            "BEGIN\n", "BEGIN\n -- unreviewed drift\n", 1
                        ),
                    )
                if blocker == "default":
                    execute_sql(
                        c,
                        m.original("mineagent_rd_json_bytes").replace(
                            "DEFAULT 0", "DEFAULT 1", 1
                        ),
                    )
                before = catalog(c)
                with pytest.raises(DBAPIError) as caught, c.begin_nested():
                    execute_sql(c, m.render_candidate_sql())
                assert caught.value.orig.sqlstate == (
                    "55P03" if blocker == "table" else "P0001"
                )
                assert catalog(c) == before
                c.rollback()


def test_full_schema_pending_root_prefix_and_savepoint(migrated_engine):
    # Actual production root/revision/steer triggers, no manufactured run,
    # candidate, SDK receipt or completed child graph.
    from tests.integration.workflow_oe_sql_fixture import declaration_statements

    with migrated_engine.connect() as c, c.begin():
        before_triggers = catalog(c)[2]
        execute_sql(c, m.render_candidate_sql())
        statements = declaration_statements()
        for _ in range(2):
            with c.begin_nested() as point:
                execute_sql(c, "\n".join(statements))
                c.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED")
                point.rollback()
            assert (
                c.exec_driver_sql(
                    "SELECT count(*) FROM research_workflows"
                ).scalar_one()
                == 0
            )
        assert catalog(c)[2] == before_triggers
        c.rollback()


def pure_install_sql():
    """Topological frozen pure closure for labelled component tables only."""
    ordered, done = [], set()

    def visit(name):
        if name in done:
            return
        done.add(name)
        source = m.original(name)
        for called in sorted(set(re.findall(r"\b(mineagent_\w+)\(", source)) - {name}):
            assert called in m.PURE
            visit(called)
        ordered.append(source)

    for name in m.PURE:
        visit(name)
    return "\n".join(ordered)


COMPONENT_TABLES = {
    "research_runs": "rvi_runs",
    "research_model_operations": "rvi_ops",
    "oe_structured_specs": "rvi_specs",
}


def component_check_sql():
    return "\n".join(
        f"ALTER TABLE {COMPONENT_TABLES[table]} ADD CONSTRAINT {name} CHECK ({expression});"
        for table, name, expression in m.CONSTRAINTS
    )


@pytest.fixture
def component_db(postgres_engine):
    # Explicitly not the application schema. Real frozen validators, synthetic
    # tables, no routing doubles and no claim of graph/receipt acceptance.
    with postgres_engine.begin() as c:
        execute_sql(c, pure_install_sql())
        c.exec_driver_sql("""
        CREATE TABLE rvi_runs(id integer PRIMARY KEY,purpose text NOT NULL,engine text NOT NULL,config jsonb NOT NULL,lifecycle integer NOT NULL DEFAULT 0);
        CREATE TABLE rvi_ops(id integer PRIMARY KEY,protocol text NOT NULL,request jsonb NOT NULL,lifecycle integer NOT NULL DEFAULT 0);
        CREATE TABLE rvi_specs(id integer PRIMARY KEY,config jsonb NOT NULL,request jsonb NOT NULL,config_canonical text NOT NULL,request_canonical text NOT NULL,config_sha256 text NOT NULL,request_sha256 text NOT NULL);
        """)
    return postgres_engine


def row_corpus():
    from packages.rdagent_provider.contracts import (
        RDProviderChatRequest,
        RDProviderEmbeddingRequest,
    )
    from tests.openevolve.test_structured_persisted_identity import graph
    from tests.openevolve.test_workflow_run import config as oe_workflow
    from tests.rdagent.test_rdagent_workflow_child_contracts import (
        config as rd_workflow,
    )
    from tests.research_workflow.test_plan import plan

    rd = [
        plan().research_template.model_dump(mode="json"),
        rd_workflow().model_dump(mode="json"),
    ]
    chat = RDProviderChatRequest(
        model_profile_id="row-invariant",
        model_binding_sha256="a" * 64,
        model="chat",
        messages=({"role": "user", "content": "中文😀"},),
        max_output_tokens=100,
        response_format=None,
    ).model_dump(mode="json")
    embedding = RDProviderEmbeddingRequest(
        model_profile_id="row-invariant",
        model_binding_sha256="a" * 64,
        model="embed",
        inputs=("中文😀",),
        dimensions=2,
    ).model_dump(mode="json")
    _, _, spec, *_ = graph()
    oe = [(spec.config, spec.request)]
    cfg = oe_workflow()
    request = copy.deepcopy(spec.request)
    request["schema_version"] = "mineagent.oe-structured-workflow-submission.v1"
    request["profile"] = "workflow_fixed_factor_operator_closed_program_v1"
    request["snapshot_id"] = cfg["fixed_inputs"]["snapshot_id"]
    request["workflow_context"] = cfg["workflow_context"]
    oe.append((cfg, request))
    return rd, [chat, embedding], oe


def insert_run(c, identifier, config, purpose="factor_research", engine="rdagent"):
    c.execute(
        text(
            "INSERT INTO rvi_runs(id,purpose,engine,config) VALUES(:id,:purpose,:engine,CAST(:config AS jsonb))"
        ),
        {
            "id": identifier,
            "purpose": purpose,
            "engine": engine,
            "config": json.dumps(config),
        },
    )


def insert_op(c, identifier, request, protocol=None):
    c.execute(
        text(
            "INSERT INTO rvi_ops(id,protocol,request) VALUES(:id,:protocol,CAST(:request AS jsonb))"
        ),
        {
            "id": identifier,
            "protocol": protocol or request["schema_version"],
            "request": json.dumps(request),
        },
    )


def spec_values(config, request):
    cfg = canonical_bytes(config)
    req = canonical_bytes(request)
    return {
        "config": cfg.decode(),
        "request": req.decode(),
        "config_canonical": cfg.decode(),
        "request_canonical": req.decode(),
        "config_sha256": hashlib.sha256(cfg).hexdigest(),
        "request_sha256": hashlib.sha256(req).hexdigest(),
    }


def insert_spec(c, identifier, values):
    c.execute(
        text("""INSERT INTO rvi_specs(id,config,request,config_canonical,request_canonical,config_sha256,request_sha256)
     VALUES(:id,CAST(:config AS jsonb),CAST(:request AS jsonb),:config_canonical,:request_canonical,:config_sha256,:request_sha256)"""),
        {"id": identifier, **values},
    )


def rejection(c, operation, constraint=None):
    with pytest.raises((DBAPIError, psycopg.Error)) as caught, c.begin_nested():
        operation()
    error = caught.value.orig if isinstance(caught.value, DBAPIError) else caught.value
    assert error.sqlstate in ("23514", "23502", "22P02")
    if constraint:
        assert error.diag.constraint_name == constraint


def test_component_local_corpus_insert_update_copy_and_savepoint(component_db):
    rd, requests, oe = row_corpus()
    with component_db.begin() as c:
        execute_sql(c, component_check_sql())
        for i, config in enumerate(rd):
            insert_run(c, i, config)
            for field, value in (
                ("task", None),
                ("budget", {}),
                ("input", None),
                ("model_binding", {}),
                ("max_iterations", 0),
            ):
                bad = {**config, field: value}
                rejection(
                    c, lambda bad=bad: insert_run(c, 100, bad), "ck_rvi_rd_run_config"
                )
        for i, request in enumerate(requests):
            insert_op(c, i, request)
            for bad in (
                None,
                {},
                [],
                {**request, "kind": "wrong"},
                {**request, "schema_version": "wrong"},
                {**request, "model": ""},
            ):
                rejection(
                    c,
                    lambda bad=bad, request=request: insert_op(
                        c, 100, bad, request["schema_version"]
                    ),
                    "ck_rvi_rd_provider_payload",
                )
        for i, (config, request) in enumerate(oe):
            values = spec_values(config, request)
            insert_spec(c, i, values)
            for field, value, check in (
                ("config", "null", "ck_rvi_oe_spec_config"),
                ("request", "null", "ck_rvi_oe_spec_request"),
                ("config_sha256", "0" * 64, "ck_rvi_oe_spec_config_raw"),
                ("request_sha256", "0" * 64, "ck_rvi_oe_spec_request_raw"),
                (
                    "config_canonical",
                    " " + values["config_canonical"],
                    "ck_rvi_oe_spec_config_raw",
                ),
                (
                    "request_canonical",
                    " " + values["request_canonical"],
                    "ck_rvi_oe_spec_request_raw",
                ),
            ):
                # Config/request JSON-null also fails its commitment. Constraint
                # evaluation order is PostgreSQL-owned, so don't overclaim which
                # independently sufficient check fires first in those two cases.
                rejection(
                    c,
                    lambda values=values, field=field, value=value: insert_spec(
                        c, 100, {**values, field: value}
                    ),
                    None if field in ("config", "request") else check,
                )
        # Schema discriminator/null/non-target branches are deliberately not
        # tightened by this additive constraint. Existing production CHECKs
        # still decide whether these are valid application rows.
        for i, value in enumerate(
            (
                None,
                {},
                [],
                {"schema_version": None},
                {"schema_version": "mineagent.factor-research.v1"},
                {"schema_version": "unknown"},
            ),
            10,
        ):
            insert_run(c, i, value)
        insert_run(
            c,
            20,
            {"schema_version": "mineagent.factor-research.v2"},
            "other",
            "rdagent",
        )
        insert_run(
            c,
            21,
            {"schema_version": "mineagent.factor-research.v3"},
            "factor_research",
            "other",
        )
        insert_op(c, 20, None, "other-protocol")
        # Lifecycle writes revalidate exactly the same immutable JSON.
        c.exec_driver_sql("UPDATE rvi_runs SET lifecycle=lifecycle+1 WHERE id<2")
        c.exec_driver_sql("UPDATE rvi_ops SET lifecycle=lifecycle+1 WHERE id<2")
        rejection(
            c,
            lambda: c.exec_driver_sql(
                "UPDATE rvi_ops SET request='null'::jsonb WHERE id=0"
            ),
            "ck_rvi_rd_provider_payload",
        )
        # SQL NULL is still rejected independently of JSON null.
        rejection(
            c,
            lambda: c.exec_driver_sql(
                "INSERT INTO rvi_runs VALUES(99,'factor_research','rdagent',NULL,0)"
            ),
        )
        with c.begin_nested() as point:
            insert_run(c, 30, rd[0])
            point.rollback()
        insert_run(c, 30, rd[0])
        # psycopg COPY follows normal immediate CHECK semantics.
        rejection(c, lambda: copy_bad_op(c), "ck_rvi_rd_provider_payload")


def copy_bad_op(c):
    with (
        c.connection.driver_connection.cursor() as cursor,
        cursor.copy("COPY rvi_ops(id,protocol,request) FROM STDIN") as stream,
    ):
        stream.write_row((101, "mineagent.rdagent-provider-chat.v1", "{}"))


@pytest.mark.parametrize("invariant", range(6))
def test_component_stale_validated_helper_rejects_historical_row_atomically(
    component_db, invariant
):
    """A synthetic reproduction of the historical-function-replacement hazard.

    These are real PG CHECK scans using exact candidate expressions, but the
    application graph is not populated. Full historical production data across
    retained026/027c/029/031 remains an explicit integration gap.
    """
    rd, requests, oe = row_corpus()
    table, name, expression = m.CONSTRAINTS[invariant]
    helper = (
        "mineagent_rd_provider_config"
        if invariant == 0
        else "mineagent_rd_provider_payload"
        if invariant == 1
        else "mineagent_oes_contract"
        if invariant in (2, 3)
        else "mineagent_structured_raw_commitment"
    )
    original = m.original(helper)
    permissive = original.split("AS $$", 1)[0] + "AS $$ BEGIN RETURN true; END $$;"
    with component_db.begin() as c:
        execute_sql(c, permissive)
        target = COMPONENT_TABLES[table]
        execute_sql(
            c,
            f"ALTER TABLE {target} ADD CONSTRAINT stale_validated_helper CHECK ({expression});",
        )
        if invariant == 0:
            insert_run(c, 0, {**rd[0], "task": None})
        elif invariant == 1:
            insert_op(c, 0, {}, requests[0]["schema_version"])
        else:
            values = spec_values(*oe[0])
            field = {
                2: "config",
                3: "request",
                4: "config_sha256",
                5: "request_sha256",
            }[invariant]
            values[field] = "null" if invariant in (2, 3) else "0" * 64
            insert_spec(c, 0, values)
        execute_sql(c, original)
        assert (
            c.execute(
                text(
                    "SELECT convalidated FROM pg_constraint WHERE conrelid=CAST(:table AS regclass) AND conname='stale_validated_helper'"
                ),
                {"table": target},
            ).scalar_one()
            is True
        )
        # VALIDATE on an already validated CHECK is a no-op, not a rescan.
        c.exec_driver_sql(
            f"ALTER TABLE {target} VALIDATE CONSTRAINT stale_validated_helper"
        )
        before = catalog(c)
        with pytest.raises(DBAPIError) as caught, c.begin_nested():
            execute_sql(
                c, f"ALTER TABLE {target} ADD CONSTRAINT {name} CHECK ({expression});"
            )
            pytest.fail("Historical invalid row was accepted")
        assert caught.value.orig.sqlstate == "23514"
        assert caught.value.orig.diag.constraint_name == name
        assert catalog(c) == before


def test_component_nonconstant_read_savings_and_write_overhead(component_db, capsys):
    """Diagnostic, three samples; no timing assertion or native receipt claim.

    Before = predecessor's exact four same-spec validations on each fetched
    row. After = same fetched rows covered by the four installed CHECKs. This
    isolates the removable component, NOT the remaining graph costs. DML time
    includes all six candidate checks on their synthetic row counterparts.
    """
    rd, requests, oe = row_corpus()
    samples = []
    with component_db.begin() as c:
        for phase in ("before", "after"):
            if phase == "after":
                execute_sql(c, component_check_sql())
            for sample in range(3):
                c.exec_driver_sql("TRUNCATE rvi_runs,rvi_ops,rvi_specs")
                start = time.perf_counter()
                for i in range(8):
                    cfg = copy.deepcopy(rd[i % 2])
                    cfg["task"]["name"] += str(i)
                    req = copy.deepcopy(requests[i % 2])
                    req["model"] += str(i)
                    insert_run(c, i, cfg)
                    insert_op(c, i, req)
                    config, request = copy.deepcopy(oe[i % 2])
                    # A valid nonconstant ordinary budget value, committed in
                    # fresh raw bytes/hash; no workflow context is fabricated.
                    config["budget"]["cost_microunits"] += i
                    insert_spec(c, i, spec_values(config, request))
                written = time.perf_counter() - start
                predicate = (
                    "true"
                    if phase == "after"
                    else """mineagent_oes_contract('config',s.config) IS TRUE
                 AND mineagent_oes_contract('request',s.request) IS TRUE
                 AND mineagent_structured_raw_commitment(s.config_canonical,s.config,s.config_sha256,1048576) IS TRUE
                 AND mineagent_structured_raw_commitment(s.request_canonical,s.request,s.request_sha256,65536) IS TRUE"""
                )
                start = time.perf_counter()
                count, checksum = c.exec_driver_sql(
                    f"SELECT count(*) FILTER(WHERE {predicate}),sum(s.id*17+i) FROM rvi_specs s CROSS JOIN generate_series(1,8) i"
                ).one()
                elapsed = time.perf_counter() - start
                assert count == 64 and checksum == 4096
                samples.append(
                    {
                        "phase": phase,
                        "sample": sample,
                        "rows_written_per_table": 8,
                        "fetched_spec_rows": count,
                        "write_ms": 1000 * written,
                        "same_spec_read_ms": 1000 * elapsed,
                    }
                )
    report = export_component_measurements("minimal", samples)
    with capsys.disabled():
        print(
            "RVI_COMPONENT_ONLY " + json.dumps(report, sort_keys=True, allow_nan=False)
        )


def test_full_schema_scan_admitted_queued_row_and_state_only_dml(
    api, migrated_engine, settings, owner
):
    """Actual historical queued declaration, full production triggers, no Docker.

    This exercises a real v2 row plus its original durable submission/budget/
    outbox graph. It is not a workflow-child/provider-operation/native fixture.
    """
    from uuid import UUID, uuid4

    from rd_configured_dataset_fixture import persist_configured_dataset
    from rd_submission_fixture import body, login, profile, submit
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(migrated_engine, expire_on_commit=False)
    dataset, _source, policy = persist_configured_dataset(
        factory, owner.user.id, settings
    )
    settings.rdagent_research_enabled = True
    settings.rdagent_runtime_profile = profile(actual_images=False)
    receipt, _exact = submit(
        api, login(api, owner), body(dataset, policy), key="rvi-historical-queued"
    )
    run_id = UUID(receipt["run_id"])
    with migrated_engine.connect() as c, c.begin():
        triggers = catalog(c)[2]
        execute_sql(c, m.render_candidate_sql())
        c.execute(
            text("SELECT mineagent_rd_provider_queued_config(:id)"), {"id": run_id}
        )
        c.execute(text("SELECT mineagent_rd_provider_graph(:id)"), {"id": run_id})
        for _ in range(2):
            with c.begin_nested() as point:
                c.execute(
                    text("UPDATE research_runs SET updated_at=updated_at WHERE id=:id"),
                    {"id": run_id},
                )
                c.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
                c.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED")
                point.rollback()
        with pytest.raises(DBAPIError), c.begin_nested():
            c.execute(
                text(
                    "UPDATE research_runs SET config=jsonb_set(config,'{task}','null'::jsonb) WHERE id=:id"
                ),
                {"id": run_id},
            )
        with pytest.raises(DBAPIError), c.begin_nested():
            c.execute(
                text("""INSERT INTO research_runs SELECT (jsonb_populate_record(NULL::research_runs,
             to_jsonb(r)||jsonb_build_object('id',CAST(:new AS text),'idempotency_key','rvi-invalid-copy',
             'config',jsonb_set(r.config,'{task}','null'::jsonb)))).* FROM research_runs r WHERE id=:old"""),
                {"new": uuid4(), "old": run_id},
            )
            c.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
        assert catalog(c)[2] == triggers
        c.rollback()
