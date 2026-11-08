"""Real PostgreSQL bounded-reference parity and complete RD-config measurements."""

import json
import os
import statistics
import time

import pytest
from sqlalchemy import text

from backend.app import schema_reference_cost_sql as module
from backend.app.rdagent_provider_revision import function, literal
from tests.integration.test_rdagent_validation_performance import (
    config_corpus,
    schema_pair,
)
from tests.integration.test_rdagent_validation_performance import (
    test_full_config_structurals_and_negative_mutations as full_config_corpus,
)
from tests.integration.test_rdagent_validation_performance import (
    test_schema_union_branch_recovery_and_malformed_rules as malformed_corpus,
)

pytestmark = pytest.mark.postgres


@pytest.fixture
def validation_db(postgres_engine):
    with postgres_engine.begin() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
        sql = "\n".join(
            (
                function(
                    literal("025_structured_factor_strategy.py"),
                    "mineagent_structured_json_value",
                ),
                module.schema_original().replace(module.NAME, "old_rd_schema"),
                module.render_upgrade_sql(),
            )
        )
        connection.exec_driver_sql(
            sql.replace("@SCHEMA@", schema), execution_options={"no_parameters": True}
        )
    return postgres_engine


def test_complete_rd_configs_and_negative_mutations(validation_db):
    full_config_corpus(validation_db)


def test_original_malformed_union_and_null_corpus(validation_db):
    malformed_corpus(validation_db)


def test_reference_depth_boundary_chains_cycles_and_bad_targets(validation_db):
    with validation_db.begin() as connection:
        for count in (0, 1, 23, 24, 25, 26):
            defs = {str(i): {"$ref": f"#/$defs/{i + 1}"} for i in range(count)}
            defs[str(count)] = {"type": "string", "minLength": 1}
            for depth in (-1, 0, 1, 23, 24, 25, None):
                schema_pair(
                    connection, "value", {"$ref": "#/$defs/0"}, defs, depth=depth
                )
        for defs in (
            {},
            None,
            [],
            {"x": None},
            {"x": {}},
            {"x": {"$ref": "#/$defs/missing"}},
        ):
            for depth in (0, 23, 24, None, -1):
                schema_pair(
                    connection, "value", {"$ref": "#/$defs/x"}, defs, depth=depth
                )
        # Tail resolution must preserve the deepest call's data_exception
        # recovery, including continuation to a successful union sibling.
        bad_ref = {"$ref": "#/$defs/bad_alias"}
        good_ref = {"$ref": "#/$defs/good_alias"}
        for bad_rule in (
            {"type": "string", "format": "date"},
            {"type": "string", "pattern": "["},
            {"type": "string", "enum": 1},
        ):
            defs = {
                "bad_alias": {"$ref": "#/$defs/bad"},
                "bad": bad_rule,
                "good_alias": {"$ref": "#/$defs/good"},
                "good": {"type": "string"},
            }
            for depth in (-1, 0, 21, 22, None):
                assert schema_pair(
                    connection, "2024-02-30", bad_ref, defs, depth=depth
                ) == ("ok", False)
                for union in ("anyOf", "oneOf"):
                    for branches in ([bad_ref, good_ref], [good_ref, bad_ref]):
                        # Union descent plus two refs reaches the scalar at
                        # depth+3: 24 is valid, while 25 must reject both arms.
                        assert schema_pair(
                            connection,
                            "2024-02-30",
                            {union: branches},
                            defs,
                            depth=depth,
                        ) == ("ok", depth is None or depth <= 21)
        cycle = {"x": {"$ref": "#/$defs/y"}, "y": {"$ref": "#/$defs/x"}}
        for depth in (0, 1, 23, 24):
            assert schema_pair(
                connection, "value", {"$ref": "#/$defs/x"}, cycle, depth=depth
            ) == ("ok", False)
        # Resource-limit behavior is not an exact portable recursion-depth
        # contract. Both unbounded NULL and very negative cycles still reject.
        for depth in (None, -10000):
            assert (
                schema_pair(
                    connection, "value", {"$ref": "#/$defs/x"}, cycle, depth=depth
                )[0]
                == "error"
            )


def test_legacy_fallback_isolated_from_bounded_calls_and_roundtrip(validation_db):
    with validation_db.begin() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
        value, rule = next(config_corpus())
        assert schema_pair(connection, value, rule, rule.get("$defs", {})) == (
            "ok",
            True,
        )
        for sql in (module.render_downgrade_sql(), module.render_upgrade_sql()):
            connection.exec_driver_sql(
                sql.replace("@SCHEMA@", schema),
                execution_options={"no_parameters": True},
            )
            assert schema_pair(connection, value, rule, rule.get("$defs", {})) == (
                "ok",
                True,
            )
        # Sentinel replaces only the new helper inside this rollback-only test.
        # Ordinary paths never touch it. Legacy roots still validate locally,
        # while their existing recursive edges stay frozen even crossing zero.
        connection.exec_driver_sql(
            f"CREATE OR REPLACE FUNCTION {schema}.{module.HELPER}(v jsonb, rule jsonb, defs jsonb, depth integer DEFAULT 0) RETURNS boolean LANGUAGE plpgsql IMMUTABLE AS $$ BEGIN RAISE EXCEPTION 'legacy-reference-sentinel'; END $$"
        )
        assert schema_pair(
            connection, "x", {"$ref": "#/$defs/x"}, {"x": {"type": "string"}}
        ) == ("ok", True)
        from tests.integration.test_rdagent_validation_performance import outcome

        for depth in (None, -1):
            assert outcome(
                connection,
                "SELECT mineagent_rd_schema(CAST(:value AS jsonb),CAST(:rule AS jsonb), '{}'::jsonb,:depth)",
                {
                    "value": json.dumps("x"),
                    "rule": json.dumps({"type": "string"}),
                    "depth": depth,
                },
            ) == ("ok", True)
            assert outcome(
                connection,
                "SELECT mineagent_rd_schema(CAST(:value AS jsonb),CAST(:rule AS jsonb),CAST(:defs AS jsonb),:depth)",
                {
                    "value": json.dumps(["x"]),
                    "rule": json.dumps(
                        {"type": "array", "items": {"$ref": "#/$defs/x"}}
                    ),
                    "defs": json.dumps({"x": {"type": "string"}}),
                    "depth": depth,
                },
            ) == ("error", "P0001")
            assert outcome(
                connection,
                'SELECT mineagent_rd_schema(\'"x"\'::jsonb,\'{"$ref":"#/$defs/x"}\'::jsonb,\'{"x":{"type":"string"}}\'::jsonb,:depth)',
                {"depth": depth},
            ) == ("error", "P0001")


def test_opt_in_complete_rd_config_benchmark(validation_db, capsys):
    if os.environ.get("MINEAGENT_SQL_COST_BENCHMARK") != "1":
        pytest.skip("Explicit real PostgreSQL microbenchmark opt-in required")
    reports = []
    with validation_db.begin() as connection:
        for label, (value, rule) in zip(
            ("rd_provider_config", "rd_workflow_config"), config_corpus()
        ):
            defs = rule.get("$defs", {})
            assert schema_pair(connection, value, rule, defs) == ("ok", True)
            params = {
                "v": json.dumps(value),
                "r": json.dumps(rule),
                "defs": json.dumps(defs),
            }
            samples = {"old": [], "new": []}
            for _ in range(7):
                for variant, function_name in (
                    ("old", "old_rd_schema"),
                    ("new", module.NAME),
                ):
                    start = time.perf_counter()
                    for _ in range(10):
                        assert (
                            connection.scalar(
                                text(
                                    f"SELECT {function_name}(CAST(:v AS jsonb),CAST(:r AS jsonb),CAST(:defs AS jsonb),0)"
                                ),
                                params,
                            )
                            is True
                        )
                    samples[variant].append((time.perf_counter() - start) * 100)
            old, new = (
                statistics.median(samples["old"]),
                statistics.median(samples["new"]),
            )
            reports.append(
                {
                    "workload": label,
                    "old_median_ms": old,
                    "new_median_ms": new,
                    "ratio": new / old,
                }
            )
    with capsys.disabled():
        print("SCHEMA_REFERENCE_COST_BENCHMARK:" + json.dumps(reports, sort_keys=True))
