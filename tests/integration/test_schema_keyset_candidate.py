"""Disposable real-PG parity/benchmarks. Never receipt/deadline acceptance.

Set TEST_DATABASE_URL to the already-authorized PostgreSQL executor. Opt in to
measurements with MINEAGENT_SQL_COST_BENCHMARK=1. Tests never provision a server.
"""

import ast
import copy
import hashlib
import itertools
import json
import os
import random
import re
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.app.rdagent_provider_revision import function, literal
from script import schema_keyset_candidate as candidate
from tests.integration.test_rdagent_validation_performance import (
    config_corpus,
    outcome,
    schema_pair,
)
from tests.integration.test_rdagent_validation_performance import (
    test_full_config_structurals_and_negative_mutations as full_corpus,
)
from tests.integration.test_rdagent_validation_performance import (
    test_schema_union_branch_recovery_and_malformed_rules as malformed_corpus,
)
from tests.integration.test_schema_reference_cost import (
    test_reference_depth_boundary_chains_cycles_and_bad_targets as reference_corpus,
)

pytestmark = pytest.mark.postgres
SQL_NULL = object()
KEYS = [
    "",
    "a",
    "A",
    "aa",
    "b",
    "a ",
    " a",
    "1",
    "01",
    "10",
    "2",
    "é",
    "e\u0301",
    "É",
    "中",
    "😀",
    "\ue000",
    "\U00010000",
    "\U0010ffff",
    "I",
    "i",
    "İ",
    "ı",
    "ß",
    "ss",
    "a\n",
    '"',
    "\\",
    "\x01",
    "\x7f",
    "\u2028",
    "\u2029",
    "a" * 127,
    "a" * 128,
    "中" * 43,
]


@pytest.fixture
def validation_db(postgres_engine):
    with postgres_engine.begin() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
        assert (
            connection.scalar(
                text(
                    "SELECT collisdeterministic FROM pg_collation WHERE oid='pg_catalog.default'::regcollation"
                )
            )
            is True
        )
        sql = "\n".join(
            (
                function(
                    literal("025_structured_factor_strategy.py"),
                    "mineagent_structured_json_value",
                ),
                candidate.baseline_schema_sql(),
                candidate.dependencies_sql(),
                candidate.schema_sql(),
            )
        )
        connection.exec_driver_sql(
            sql.replace("@SCHEMA@", schema), execution_options={"no_parameters": True}
        )
    return postgres_engine


def test_old_complete_config_corpus(validation_db):
    full_corpus(validation_db)


def test_old_malformed_union_corpus(validation_db):
    malformed_corpus(validation_db)


def test_old_reference_depth_corpus(validation_db):
    reference_corpus(validation_db)


def primitive_expressions():
    # These expressions are copied from the frozen/candidate comparison,
    # not independent approximations with different NULL or collation rules.
    return tuple(
        expr.replace("rule->'properties'", "b")
        .replace("(v,", "(a,")
        .replace("(v)", "(a)")
        for expr in (candidate.OLD, candidate.NEW)
    )


def key_cases():
    """Exactly equal sets, unequal same-size sets, subsets and reordered values."""
    yield {}, {}, False
    for left, right in itertools.product(KEYS, repeat=2):
        yield {left: [1, {"nested": True}]}, {right: None}, left != right
    rng = random.Random(320032)
    for i in range(1000):
        keys = rng.sample(KEYS, rng.randrange(len(KEYS) + 1))
        left = {key: {"value": i, "nested": [None, True, "😀"]} for key in keys}
        right_keys = keys[:]
        rng.shuffle(right_keys)
        right = {key: "different" for key in right_keys}
        if i % 3 == 0:
            right["extra" + str(i)] = False
        elif i % 3 == 1 and keys:
            right.pop(keys[0])
        yield left, right, left.keys() != right.keys()
    for count in (1, 2, 31, 32, 127, 128, 1024):
        left = {f"k{i:05d}": "x" * (i % 65) for i in range(count)}
        yield left, {key: None for key in reversed(left)}, False
    # Large nested values must not change the outer key sequence.
    yield {"x": ["é" * 10000] * 10, "bb": {}}, {"bb": [None], "x": False}, False
    for left, right in (
        (SQL_NULL, SQL_NULL),
        (SQL_NULL, {}),
        ({}, SQL_NULL),
        (SQL_NULL, {"x": 1}),
        ({"x": 1}, SQL_NULL),
    ):
        yield (
            left,
            right,
            (left is not SQL_NULL and bool(left))
            or (right is not SQL_NULL and bool(right)),
        )


def encoded(value):
    return None if value is SQL_NULL else json.dumps(value, ensure_ascii=True)


def test_table_backed_random_unicode_keysets(validation_db):
    old, new = primitive_expressions()
    with validation_db.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TEMP TABLE key_cases (id integer,a jsonb,b jsonb,expected boolean) ON COMMIT DROP"
        )
        rows = [
            {"id": i, "a": encoded(a), "b": encoded(b), "e": expected}
            for i, (a, b, expected) in enumerate(key_cases())
        ]
        connection.execute(
            text(
                "INSERT INTO key_cases VALUES (:id,CAST(:a AS jsonb),CAST(:b AS jsonb),:e)"
            ),
            rows,
        )
        failures = connection.exec_driver_sql(
            f"SELECT id,({old}) AS old,({new}) AS new,expected FROM key_cases WHERE ({old}) IS DISTINCT FROM expected OR ({new}) IS DISTINCT FROM expected"
        ).all()
        assert not failures
        # Storage order depends on UTF-8 bytes, not codepoints, values, insertion
        # order, C lexical order, or locale-aware alphabetical order.
        actual = connection.scalar(
            text("SELECT ARRAY(SELECT jsonb_object_keys(CAST(:v AS jsonb)))"),
            {"v": encoded({key: i for i, key in enumerate(KEYS)})},
        )
        assert actual == sorted(KEYS, key=lambda key: (len(key.encode()), key.encode()))
        assert actual != sorted(KEYS, key=lambda key: key.encode())
        assert (
            connection.scalar(
                text(
                    'SELECT pg_collation_for((SELECT array_agg(key ORDER BY key COLLATE "C") FROM jsonb_object_keys(CAST(:v AS jsonb)) key))'
                ),
                {"v": '{"a":1}'},
            )
            == '"default"'
        )
        # Duplicate raw keys have already been collapsed identically by jsonb.
        assert connection.scalar(
            text("SELECT ARRAY(SELECT jsonb_object_keys(CAST(:v AS jsonb)))"),
            {"v": '{"a":1,"a":false,"bb":null}'},
        ) == ["a", "bb"]


def test_table_plan_removes_sorts_and_aggregate_nodes(validation_db):
    def node_types(plan):
        yield plan["Node Type"]
        for child in plan.get("Plans", []):
            yield from node_types(child)

    with validation_db.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TEMP TABLE key_plan(a jsonb,b jsonb) ON COMMIT DROP"
        )
        connection.execute(
            text("INSERT INTO key_plan VALUES (CAST(:a AS jsonb),CAST(:b AS jsonb))"),
            {"a": '{"x":true}', "b": '{"x":false}'},
        )
        plans = [
            connection.exec_driver_sql(
                f"EXPLAIN (FORMAT JSON) SELECT ({expr}) FROM key_plan"
            ).scalar_one()[0]["Plan"]
            for expr in primitive_expressions()
        ]
        old_nodes, new_nodes = (list(node_types(plan)) for plan in plans)
        assert old_nodes.count("Aggregate") == 2
        assert "Aggregate" not in new_nodes
        assert "Sort" not in new_nodes
        assert new_nodes.count("ProjectSet") == 2


def test_nonobjects_keep_data_exception_recovery(validation_db):
    old, new = primitive_expressions()
    invalid = [None, [], [{}], [{"x": 1}], "", "x", 0, 1.25, True, False]
    with validation_db.begin() as connection:
        for a, b in itertools.product([{}, {"x": 1}, SQL_NULL, *invalid], repeat=2):
            if not any(v is not SQL_NULL and not isinstance(v, dict) for v in (a, b)):
                continue
            results = [
                outcome(
                    connection,
                    f"SELECT ({expr}) FROM (SELECT CAST(:a AS jsonb) a,CAST(:b AS jsonb) b) inputs",
                    {"a": encoded(a), "b": encoded(b)},
                )
                for expr in (old, new)
            ]
            # Both paths call the same SRF and preserve its exact SQLSTATE.
            assert results[0] == results[1]
            assert all(
                result[0] == "error" and result[1].startswith("22")
                for result in results
            ), (a, b, results)
        for properties in invalid:
            bad = {"type": "object", "properties": properties}
            good = {"type": "object", "properties": {}}
            for depth in (-1, 0, 23, 24, 25, None):
                assert schema_pair(connection, {}, bad, depth=depth) == ("ok", False)
            for union in ("anyOf", "oneOf"):
                for branches in ([bad, good], [good, bad]):
                    assert schema_pair(connection, {}, {union: branches}) == (
                        "ok",
                        True,
                    )
                    assert schema_pair(
                        connection,
                        {"x": {}},
                        {"type": "object", "properties": {"x": {union: branches}}},
                    ) == ("ok", True)
        # Preserve the surprising legacy acceptance of absent properties on {}.
        for rule in ({"type": "object"}, {"type": "object", "properties": {}}):
            assert schema_pair(connection, {}, rule) == ("ok", True)
            assert schema_pair(connection, {"x": 1}, rule) == ("ok", False)
        assert schema_pair(connection, None, {"type": "object"}, sql_null=True) == (
            "ok",
            False,
        )


def test_caller_nondeterministic_collation_cannot_leak_into_jsonb(validation_db):
    with validation_db.begin() as connection:
        if not connection.scalar(
            text("SELECT EXISTS(SELECT 1 FROM pg_collation WHERE collprovider='i')")
        ):
            pytest.skip(
                "ICU unavailable; explicit nondeterministic collation probe not run"
            )
        connection.exec_driver_sql(
            "CREATE COLLATION candidate_nondeterministic (provider=icu, locale='und-u-ks-level1', deterministic=false)"
        )
        assert (
            connection.scalar(
                text(
                    "SELECT 'é' COLLATE candidate_nondeterministic = 'e' COLLATE candidate_nondeterministic"
                )
            )
            is True
        )
        for a, b in (("é", "e"), ("é", "e\u0301"), ("A", "a"), ("I", "i")):
            params = {
                "v": encoded({a: True}),
                "r": encoded(
                    {"type": "object", "properties": {b: {"type": "boolean"}}}
                ),
            }
            for name in ("old_rd_schema", candidate.NAME):
                assert (
                    connection.scalar(
                        text(
                            f"SELECT {name}(CAST(CAST(:v AS text) COLLATE candidate_nondeterministic AS jsonb),CAST(CAST(:r AS text) COLLATE candidate_nondeterministic AS jsonb),'{{}}'::jsonb,0)"
                        ),
                        params,
                    )
                    is False
                )


def test_unicode_closed_schema_and_depth_differential(validation_db):
    with validation_db.begin() as connection:
        for keys in (KEYS, list(reversed(KEYS)), KEYS[::2]):
            value = {key: True for key in keys}
            rule = {
                "type": "object",
                "properties": {key: {"type": "boolean"} for key in reversed(keys)},
            }
            for depth in (-1, 0, 23, 24, None):
                schema_pair(connection, value, rule, depth=depth)
            for alternate in (
                {},
                {**value, "extra": True},
                {**value, keys[0]: False},
                {**value, keys[0]: 1},
            ):
                schema_pair(connection, alternate, rule)
        value, rule = (
            {"a": True},
            {"type": "object", "properties": {"a": {"type": "boolean"}}},
        )
        for level in range(27):
            schema_pair(connection, value, rule)
            if level % 2:
                value, rule = [value], {"type": "array", "items": rule}
            else:
                value, rule = (
                    {"é": value},
                    {"type": "object", "properties": {"é": rule}},
                )


CONTRACT_PINS = {
    "026_rd_seeded_submissions.py": "629d918feb22c39f943bc2a7022f77f605adca28fbcce0ce5868c250815f2e2c",
    "027a_contract_repairs.py": "bb5492b3a451330bb6317352dd400cf937c4b4219def083d0146d4d5dd403ba6",
    "028_oe_structured_search.py": "dbb61d771730685de3cf8cb2fd7eeafe48e2c7c77fa54d57c19d70df2e68f7c9",
    "029_research_workflow.py": "2848ba2747b703965ad8de8048591ac1931854666cda2f1f6dd512976aaf9ed3",
    "030_declared_upload_basket.py": "1f587094ed8d30048865a13f77effdf162ec77b8c5488d70a64c7b1e94c38a7e",
    "031_workflow_structured_stage.py": "67c916d766057c38db8f9e275de1936dcc3edf7a9803931dff597ed6b912ec84",
}


def frozen_function(filename, name):
    """Read a function only from the SHA-pinned frozen migration literal."""
    path = Path(__file__).parents[2] / "backend/alembic/versions" / filename
    sql = next(
        ast.literal_eval(node.value)
        for node in ast.parse(path.read_text()).body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "UPGRADE_SQL" for t in node.targets)
    )
    assert hashlib.sha256(sql.encode()).hexdigest() == CONTRACT_PINS[filename]
    return function(sql, name)


def frozen_rule(filename, name, kind=None):
    """The exact selected installed grammar, not a current model renderer."""
    body = frozen_function(filename, name)
    prefix = (
        r"rule jsonb" if kind is None else r"WHEN '" + re.escape(kind) + r"' THEN rule"
    )
    matches = re.findall(prefix + r"\s*:=\s*'((?:''|[^'])*)'::jsonb", body)
    assert len(matches) == 1, (filename, name, kind)
    rule = json.loads(matches[0].replace("''", "'"))
    if (filename, name, kind) == (
        "027a_contract_repairs.py",
        "mineagent_structured_contract",
        "input",
    ):
        # Match the installed function's rule projection; keep DTO bytes intact.
        assert "(rule#>'{$defs,StrategyManifest,properties}')-'authoring'" in body
        assert "(rule#>'{$defs,StrategyManifest,required}')-'authoring'" in body
        manifest = rule["$defs"]["StrategyManifest"]
        del manifest["properties"]["authoring"]
        manifest["required"].remove("authoring")
    return rule


def workloads():
    from packages.openevolve_structured.objective import StructuredMeasurement
    from packages.openevolve_structured.provider_bootstrap import (
        ProviderStructuredBootstrap,
        runtime_profile_sha256,
    )
    from packages.openevolve_structured.provider_run import ProviderStructuredRunConfig
    from tests.openevolve.test_structured_persisted_identity import graph
    from tests.openevolve.test_workflow_prompt import bootstrap as workflow_bootstrap
    from tests.structured.test_engine_oracle import engine_case

    rd_values = [pair[0] for pair in config_corpus()]
    yield (
        "rd_config",
        rd_values[0],
        frozen_rule(
            "026_rd_seeded_submissions.py", "mineagent_rd_provider_payload", "config"
        ),
    )
    yield (
        "rd_workflow_config",
        rd_values[1],
        frozen_rule("029_research_workflow.py", "mineagent_rd_workflow_config"),
    )
    spec, record = graph()[2:5:2]
    config = ProviderStructuredRunConfig.model_validate_json(spec.config_canonical)
    measured = StructuredMeasurement(
        execution_id=uuid4(),
        structured_strategy_version_id=uuid4(),
        snapshot_id=uuid4(),
        strategy_input_sha256="a" * 64,
        market_sha256="b" * 64,
        result_sha256="c" * 64,
        objective_sha256="d" * 64,
        total_return_pct=4.75,
        max_drawdown_pct=0.125,
        closed_trade_count=1,
        eligible=True,
        combined_score=4.625,
        reason="eligible",
    )
    # 031 routes these retained v1 DTOs through the installed 030 shape helper.
    # Its config/request/candidate weights are positive and <=1. The older 028
    # rules incorrectly require >=1 and do not describe the installed grammar.
    for kind, value in (
        ("config", spec.config),
        ("request", spec.request),
        ("candidate", json.loads(record.candidate_canonical)),
        ("measurement", measured.model_dump(mode="json")),
    ):
        yield (
            "oe_" + kind,
            value,
            frozen_rule(
                "030_declared_upload_basket.py",
                "mineagent_oes_contract_v030_shape",
                kind,
            ),
        )
    now = datetime(2026, 1, 1, tzinfo=UTC)
    bootstrap = ProviderStructuredBootstrap(
        admission_id=uuid4(),
        epoch_id=uuid4(),
        spec=config.search,
        issued_at=now,
        not_after=now + timedelta(seconds=120),
        physical_profile_sha256="a" * 64,
        runtime_profile_sha256=runtime_profile_sha256(),
        config_sha256="b" * 64,
        adapter_patch_sha256="c" * 64,
        model_binding=config.model_binding,
        model_binding_sha256=config.model_binding.sha256,
    )
    yield (
        "oe_bootstrap",
        bootstrap.model_dump(mode="json"),
        frozen_rule("031_workflow_structured_stage.py", "mineagent_oes_epoch_check"),
    )
    yield (
        "oe_workflow_bootstrap",
        workflow_bootstrap().model_dump(mode="json"),
        frozen_rule(
            "031_workflow_structured_stage.py",
            "mineagent_oe_workflow_shape",
            "bootstrap",
        ),
    )
    inputs, _ = engine_case(2)
    yield (
        "structured_input",
        inputs.model_dump(mode="json"),
        frozen_rule(
            "027a_contract_repairs.py", "mineagent_structured_contract", "input"
        ),
    )


def test_real_dto_corpus_with_missing_extra_and_changed_values(validation_db):
    with validation_db.begin() as connection:
        for label, value, rule in workloads():
            defs = rule.get("$defs", {})
            assert schema_pair(connection, value, rule, defs) == ("ok", True), label
            missing = copy.deepcopy(value)
            del missing[next(iter(missing))]
            for invalid in (
                missing,
                {**value, "unexpected": True},
                {**value, next(iter(value)): []},
            ):
                assert schema_pair(connection, invalid, rule, defs) == ("ok", False), (
                    label
                )


def catalog(connection):
    return connection.exec_driver_sql(
        "SELECT p.proname,pg_get_functiondef(p.oid),p.proacl::text,p.proowner FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=current_schema() AND p.prokind='f' ORDER BY 1"
    ).all()


def test_exact_install_restore_roundtrip(validation_db):
    with validation_db.begin() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
        before = catalog(connection)
        for sql in (candidate.restore_schema_sql(), candidate.schema_sql()):
            connection.exec_driver_sql(
                sql.replace("@SCHEMA@", schema),
                execution_options={"no_parameters": True},
            )
            assert schema_pair(connection, {}, {"type": "object"}) == ("ok", True)
            assert schema_pair(
                connection, {}, {"type": "object", "properties": None}
            ) == ("ok", False)
        assert catalog(connection) == before


def function_counts(connection):
    # SQLAlchemy compiles literal percent signs for psycopg; do not hand the
    # LIKE patterns to exec_driver_sql's parameter parser.
    return {
        row.funcname: {
            "calls": row.calls,
            "total_ms": row.total_time,
            "self_ms": row.self_time,
        }
        for row in connection.execute(
            text(
                "SELECT funcname,calls,total_time,self_time FROM pg_stat_xact_user_functions "
                "WHERE schemaname=current_schema() "
                "AND (funcname LIKE 'old_rd_schema%' OR funcname LIKE 'mineagent_rd_schema%')"
            )
        )
    }


BENCHMARK_ENVIRONMENT_SQL = (
    "SELECT version() AS version,d.datcollate AS lc_collate,"
    "current_setting('server_encoding') AS encoding,"
    "current_setting('track_functions') AS tracking "
    "FROM pg_catalog.pg_database AS d WHERE d.datname=current_database()"
)


def test_opt_in_table_backed_workload_benchmark(validation_db, capsys):
    if os.environ.get("MINEAGENT_SQL_COST_BENCHMARK") != "1":
        pytest.skip("Explicit real PostgreSQL microbenchmark opt-in required")
    reports = []
    with validation_db.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TEMP TABLE candidate_workloads (ordinal integer,value jsonb,rule jsonb,defs jsonb) ON COMMIT DROP"
        )
        environment = (
            connection.exec_driver_sql(BENCHMARK_ENVIRONMENT_SQL).mappings().one()
        )
        for label, value, rule in workloads():
            defs = rule.get("$defs", {})
            assert schema_pair(connection, value, rule, defs) == ("ok", True)
            connection.exec_driver_sql("TRUNCATE candidate_workloads")
            connection.execute(
                text(
                    "INSERT INTO candidate_workloads SELECT i,CAST(:v AS jsonb),CAST(:r AS jsonb),CAST(:d AS jsonb) FROM generate_series(1,32) i"
                ),
                {"v": encoded(value), "r": encoded(rule), "d": encoded(defs)},
            )
            samples = {"baseline": [], "candidate": []}
            # Warm both code/plan paths before measurements. Table columns keep
            # IMMUTABLE validation out of planner-time constant folding.
            for name in ("old_rd_schema", candidate.NAME):
                assert (
                    connection.exec_driver_sql(
                        f"SELECT count(*) FILTER (WHERE {name}(value,rule,defs,0)) FROM candidate_workloads"
                    ).scalar_one()
                    == 32
                )
            before = function_counts(connection)
            for repeat in range(8):
                order = list(samples) if repeat % 2 == 0 else list(reversed(samples))
                for name in order:
                    func = "old_rd_schema" if name == "baseline" else candidate.NAME
                    plan = connection.exec_driver_sql(
                        f"EXPLAIN (ANALYZE, FORMAT JSON) SELECT count(*) FILTER (WHERE {func}(value,rule,defs,0)) FROM candidate_workloads"
                    ).scalar_one()[0]
                    samples[name].append(plan["Execution Time"])
            after = function_counts(connection)
            counters = {
                name: {key: row[key] - before.get(name, {}).get(key, 0) for key in row}
                for name, row in after.items()
            }
            baseline, proposed = (
                statistics.median(samples[name]) for name in ("baseline", "candidate")
            )
            reports.append(
                {
                    "workload": label,
                    "rows": 32,
                    "baseline_ms": baseline,
                    "candidate_ms": proposed,
                    "ratio": proposed / baseline,
                    "samples": samples,
                    "function_deltas": counters,
                }
            )
    with capsys.disabled():
        print(
            "SCHEMA_KEYSET_CANDIDATE:"
            + json.dumps(
                {"environment": dict(environment), "reports": reports}, sort_keys=True
            )
        )
