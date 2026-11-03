"""Closed disposable installer, source pins and exact pure-branch rewrite."""

from contextlib import contextmanager
from types import SimpleNamespace as NS

import pytest

from tests.integration import oe_raw_ascii_experiment as d


def row(name, sql):
    args, types, names = (
        ("v json, depth integer DEFAULT 0", "114 23", ["v", "depth"])
        if name == d.FUNCTION
        else (
            "v jsonb, ascii_only boolean, depth integer DEFAULT 0",
            "3802 16 23",
            ["v", "ascii_only", "depth"],
        )
    )
    return [
        d._body(sql.replace("@SCHEMA@", '"public"')),
        "plpgsql",
        "i",
        "u",
        True,
        False,
        False,
        False,
        "f",
        100,
        0,
        ["search_path=pg_catalog"],
        "text",
        types,
        names,
        10,
        None,
        99 if name == d.FUNCTION else 100,
        "-",
        args,
        True,
        None,
        None,
    ]


def test_exact_rewrite_and_guards():
    old, new = d.original_sql(), d.candidate_sql()
    assert old.count(d.KEY) == old.count(d.STRING) == 1
    assert new.count(d.KEY) == 1  # exact original fallback still present
    assert (
        "ELSE result:=@SCHEMA@.mineagent_rd_canonical_json(to_jsonb(token),true);"
        in new
    )
    for guard in (
        "depth<0 OR depth>24",
        "octet_length(v::text)>16777216",
        "Structured duplicate key",
        "Structured raw size",
        "current_setting('server_encoding')<>'UTF8'",
    ):
        assert old.count(guard) == new.count(guard) == 1
    assert new.index("Structured duplicate key") < new.index(d.plain("key"))
    assert new.count("<=1048574") == 2
    assert new.count("pg_catalog.strpos") == 4
    assert (
        "RETURNS text LANGUAGE plpgsql IMMUTABLE STRICT SET search_path=pg_catalog"
        in new
    )
    assert "depth integer DEFAULT 0" in new


@pytest.mark.parametrize(
    "index,value",
    [
        (1, "sql"),
        (2, "v"),
        (3, "s"),
        (4, False),
        (5, True),
        (6, True),
        (7, True),
        (8, "p"),
        (9, 1),
        (10, 1),
        (11, ["search_path=public"]),
        (12, "json"),
        (13, "3802 23"),
        (14, ["x", "depth"]),
        (16, "{public=X}"),
        (18, "other"),
        (19, "v json, depth integer DEFAULT 1"),
        (20, False),
        (21, ["i"]),
        (22, [114, 23]),
        (0, "drift"),
    ],
)
def test_catalog_drift_rejected(index, value):
    sql = d.original_sql()
    value_row = row(d.FUNCTION, sql)
    value_row[index] = value
    connection = NS(execute=lambda *_: NS(all=lambda: [value_row]))
    with pytest.raises(ValueError, match="catalog drift"):
        d.verify(connection, d.FUNCTION, sql)


def test_source_pin_drift(monkeypatch):
    monkeypatch.setattr(d, "historical", lambda *_: "private changed source")
    with pytest.raises(ValueError, match="predecessor"):
        d.candidate_sql()


@pytest.mark.parametrize("mode", ["control", "candidate"])
def test_installer_closed_and_metadata_restored(mode, monkeypatch):
    monkeypatch.setenv(d.ENV, mode)
    old, new, helper = d.original_sql(), d.candidate_sql(), d.helper_sql()
    current = {d.FUNCTION: row(d.FUNCTION, old), d.HELPER: row(d.HELPER, helper)}
    statements, lifecycle = [], []

    class Connection:
        def execute(self, query, params):
            return NS(all=lambda: [current[params["name"]]])

        def exec_driver_sql(self, sql, **kwargs):
            assert kwargs == {"execution_options": {"no_parameters": True}}
            assert sql == new.replace("@SCHEMA@", '"public"')
            statements.append(sql)
            current[d.FUNCTION] = row(d.FUNCTION, new)

    @contextmanager
    def begin():
        lifecycle.append("begin")
        try:
            yield Connection()
        except BaseException:
            lifecycle.append("rollback")
            raise
        else:
            lifecycle.append("commit")

    monkeypatch.setattr(d, "_fixture", lambda *a: None)
    report = d.prepare(NS(begin=begin), object())
    assert report["mode"] == mode
    assert report["production_acceptance"] is False
    assert report["schema_state"] == (
        "full032c_experimental_raw_ascii"
        if mode == "candidate"
        else "full032c_unmodified"
    )
    assert (
        report["raw_installed_body_sha256"]
        == d.sha256(current[d.FUNCTION][0].encode()).hexdigest()
    )
    assert (
        report["canonical_helper_body_sha256"]
        == d.sha256(current[d.HELPER][0].encode()).hexdigest()
    )
    assert len(statements) == (mode == "candidate")
    assert lifecycle == ["begin", "commit"]
    assert report["plain_input_max_bytes"] == 1048574
    assert report["canonical_output_max_bytes"] == 1048576

    # Post-DDL verification failure must escape the transaction and roll back.
    def fail_verify(*args):
        if statements:
            raise ValueError("catalog drift")
        return tuple(row(args[1], args[2]))

    if mode == "candidate":
        statements.clear()
        monkeypatch.setattr(d, "verify", fail_verify)
        with pytest.raises(ValueError, match="catalog drift"):
            d.prepare(NS(begin=begin), object())
        assert lifecycle[-1] == "rollback"


def test_disabled_invalid_and_wrong_fixture(monkeypatch):
    monkeypatch.delenv(d.ENV, raising=False)
    assert d.prepare(object(), object()) is None
    monkeypatch.setenv(d.ENV, "anything")
    with pytest.raises(ValueError, match="mode"):
        d.prepare(object(), object())
    monkeypatch.setenv(d.ENV, "candidate")

    @contextmanager
    def begin():
        yield object()

    with pytest.raises(ValueError, match="fixture"):
        d.prepare(NS(begin=begin), NS(source=NS(database="not-disposable")))


def test_explicit_experiment_workflow_runs_only_normal_flow_twice():
    from pathlib import Path

    root = Path(__file__).parents[2]
    flow = (root / ".github/workflows/mineagent-structured-factor.yml").read_text()
    assert flow.startswith(
        "name: EXPERIMENT MineAgent OE raw ASCII control versus candidate"
    )
    assert "on:\n  workflow_dispatch:\n" in flow
    assert "  push:" not in flow and "  pull_request:" not in flow
    loop = flow.split("for variant in control candidate; do", 1)[1].split(
        "          done", 1
    )[0]
    assert 'export MINEAGENT_OE_RAW_ASCII_EXPERIMENT="$variant"' in loop
    assert 'if [ "$variant" = control ]; then' in loop
    assert "selectors+=(tests/integration/test_rdagent_provider_rejection.py)" in loop
    assert "selectors=(tests/integration/test_oe_structured_normal_flow.py)" in loop
    assert "test_actual_structured_pair" not in loop
    assert 'test "$control_status" = 0' in flow
    assert 'test "$candidate_status" = 0' in flow
    test = (root / "tests/integration/test_oe_structured_normal_flow.py").read_text()
    assert (
        test.index('legacy._upgrade(engine, "032c_oe_provider_table_checks")')
        < test.index("prepare(engine, drill_databases)")
        < test.index("_run_flow(engine, settings, monkeypatch)")
    )
