"""Real PostgreSQL raw-JSON differential, rollback and measured microbenchmarks.

No synthetic relational receipt graph or runtime acceptance is claimed.
"""

import json
import os
import statistics
import time

import pytest
from sqlalchemy import text

from backend.app import structured_raw_cost_sql as module
from backend.app.rdagent_validation_performance_sql import canonical_sql
from tests.integration.test_rdagent_validation_performance import outcome

pytestmark = pytest.mark.postgres


@pytest.fixture
def raw_db(postgres_engine):
    with postgres_engine.begin() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
        sql = "\n".join(
            (
                canonical_sql(),
                module.raw_original().replace(
                    module.RAW_NAME, "old_structured_raw_json"
                ),
                module.render_upgrade_sql(),
            )
        )
        connection.exec_driver_sql(
            sql.replace("@SCHEMA@", schema), execution_options={"no_parameters": True}
        )
    return postgres_engine


def pair(connection, raw, depth=0):
    params = {"raw": raw, "depth": depth}
    query = "SELECT {}(CAST(:raw AS json), :depth)"
    old = outcome(connection, query.format("old_structured_raw_json"), params)
    new = outcome(connection, query.format(module.RAW_NAME), params)
    assert new == old
    return new


def test_scalar_unicode_number_duplicate_and_depth_parity(raw_db):
    strings = [
        "",
        "plain",
        'quotes" slash/ backslash\\',
        "".join(chr(i) for i in range(1, 32)),
        "\x7f",
        "é中",
        "😀",
        "e\u0301",
        "\u2028\u2029",
    ]
    values = [None, True, False, 0, -1, 10**100, {}, [], *strings]
    values += [[value, {"z": value}] for value in strings]
    values += [{value: value, "nested": [None, False, 123]} for value in strings]
    raws = [json.dumps(value, ensure_ascii=True) for value in values]
    tokens = [
        "-0",
        "0.0",
        "-0.0",
        "1.5",
        "1e0",
        "1e+2",
        "1e-2",
        "1E2",
        "1e309",
        "1e-9999",
        "0e-9999",
        "1.7976931348623157e308",
        "5e-324",
    ]
    raws += [
        form
        for token in tokens
        for form in (token, "[" + token + "]", '{"a":' + token + "}")
    ]
    for escaped in (r'"\ud800"', r'"\udc00"', r'"\u0000"'):
        raws += [escaped, "[" + escaped + "]", "{" + escaped + ":0}"]
    raws += [
        '{"x":1,"x":2}',
        '{"x":1,"\\u0078":2}',
        '[{"x":1,"x":2}]',
        '{"é":1,"é":2}',
        ' { "x" : [ true, null, 1.50 ] } ',
    ]
    with raw_db.begin() as connection:
        for raw in raws:
            pair(connection, raw)
        for depth in (-1, 0, 23, 24, 25, None):
            for raw in (
                None,
                "null",
                "[]",
                "{}",
                "[null]",
                '{"x":true}',
                '{"x":[false]}',
            ):
                pair(connection, raw, depth)
        for depth in (22, 23, 24, 25):
            raw = "[" * depth + '"leaf"' + "]" * depth
            pair(connection, raw)


def test_exact_input_output_and_escaped_string_size_bounds(raw_db):
    limit = 1048576
    values = ["x" * n for n in (limit - 2, limit - 1, limit, limit + 1)]
    values += ['"' * n for n in (limit // 2 - 1, limit // 2, limit // 2 + 1)]
    with raw_db.begin() as connection:
        for value in values:
            raw = json.dumps(value)
            for wrapped in (
                raw,
                "[" + raw + "]",
                "{" + raw + ":0}",
                '{"x":' + raw + "}",
            ):
                pair(connection, wrapped)
        for size in (16777216, 16777217):
            pair(connection, " " * (size - 4) + "null")
        # A small Unicode scalar adds four canonical bytes without sending a
        # large string through the Unicode fallback. Every scalar remains
        # within 1 MiB and the raw input remains below the 16 MiB input cap.
        for delta in (-1, 0, 1):
            values = ["x" * (limit - 2)] * 15 + [
                "x" * (limit - 28 + delta),
                "é",
            ]
            raw = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
            expected = json.dumps(values, ensure_ascii=True, separators=(",", ":"))
            assert len(expected.encode("utf8")) == 16777216 + delta
            assert len(raw.encode("utf8")) == 16777216 + delta - 4
            actual = pair(connection, raw)
            assert actual == (("ok", expected) if delta <= 0 else ("error", "P0001"))


def test_upgrade_downgrade_upgrade_restores_exact_function_and_bytes(raw_db):
    raw = '{"number":-0.0,"text":"line\\nquote\\"","nested":[1,true,null]}'
    with raw_db.begin() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
        expected = pair(connection, raw)
        for sql in (module.render_downgrade_sql(), module.render_upgrade_sql()):
            connection.exec_driver_sql(
                sql.replace("@SCHEMA@", schema),
                execution_options={"no_parameters": True},
            )
            assert pair(connection, raw) == expected


def test_opt_in_real_pg_raw_workload_benchmark(raw_db, capsys):
    if os.environ.get("MINEAGENT_SQL_COST_BENCHMARK") != "1":
        pytest.skip("Explicit real PostgreSQL microbenchmark opt-in required")
    from tests.openevolve.test_structured_persisted_identity import graph

    spec = graph()[2]
    workloads = {
        "oe_config": spec.config_canonical,
        "nested_scalar_population": json.dumps(
            {
                "programs": [
                    {
                        "id": str(i),
                        "code": "canonical candidate\\n" * 12,
                        "metrics": {"score": 0.25, "count": 123},
                        "flags": [True, None, False],
                    }
                    for i in range(32)
                ]
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    }
    reports = []
    with raw_db.begin() as connection:
        for name, raw in workloads.items():
            assert pair(connection, raw)[0] == "ok"
            samples = {"old": [], "new": []}
            for _ in range(7):
                for label, function in (
                    ("old", "old_structured_raw_json"),
                    ("new", module.RAW_NAME),
                ):
                    start = time.perf_counter()
                    for _ in range(10):
                        connection.scalar(
                            text(f"SELECT {function}(CAST(:raw AS json), 0)"),
                            {"raw": raw},
                        )
                    samples[label].append((time.perf_counter() - start) * 100)
            reports.append(
                {
                    "workload": name,
                    "old_median_ms": statistics.median(samples["old"]),
                    "new_median_ms": statistics.median(samples["new"]),
                    "ratio": statistics.median(samples["new"])
                    / statistics.median(samples["old"]),
                }
            )
    with capsys.disabled():
        print("STRUCTURED_RAW_COST_BENCHMARK:" + json.dumps(reports, sort_keys=True))
