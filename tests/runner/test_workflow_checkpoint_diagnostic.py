"""Pure rollback/privacy/source tests; no sockets or production SQL mutation."""

import ast
import hashlib
import importlib.util
import re
from pathlib import Path
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

spec = importlib.util.spec_from_file_location(
    "checkpoint_diag",
    Path(__file__).parents[1] / "integration/workflow_checkpoint_diagnostic.py",
)
diag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diag)
PRIVATE = "private_id_sql_parameters_and_error"


def frozen_definitions(last_revision=None):
    definitions = {}
    for path in sorted(Path("backend/alembic/versions").glob("*.py")):
        if last_revision is not None and path.stem > last_revision:
            continue
        for node in ast.parse(path.read_text()).body:
            if not (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Constant)
                and type(node.value.value) is str
                and any(
                    getattr(t, "id", "").endswith(("SQL", "_UP"))
                    and "DOWN" not in getattr(t, "id", "")
                    for t in node.targets
                )
            ):
                continue
            for match in re.finditer(
                r"CREATE (?:OR REPLACE )?FUNCTION (?:@SCHEMA@\.)?(mineagent_\w+)\([^;]*?AS \$\$(.*?)\$\$;",
                node.value.value,
                re.DOTALL,
            ):
                definitions[match[1]] = match[0], match[2]
    return definitions


def catalog():
    definitions = frozen_definitions()
    rows = []
    for name, (digest, volatility, setting) in diag.PINS.items():
        definition, body = definitions[name]
        assert hashlib.sha256(body.encode()).hexdigest() == digest
        assert not re.search(
            r"\b(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|TRUNCATE|EXECUTE|CALL)\b", body
        )
        assert "SECURITY DEFINER" not in definition
        assert set(re.findall(r"\b(mineagent_\w+)\s*\(", body)) <= diag.PINS.keys()
        actual = (
            "i"
            if "IMMUTABLE" in definition.split("AS $$")[0]
            else "s"
            if "STABLE" in definition.split("AS $$")[0]
            else "v"
        )
        assert actual == volatility
        header = definition.split("AS $$")[0]
        assert setting == (
            "schema"
            if "SET search_path=@SCHEMA@,pg_catalog" in header
            else "catalog"
            if "SET search_path=pg_catalog" in header
            else "none"
        )
        rows.append((name, body, False, volatility, setting))
    assert len(rows) == 58
    # Verify the exact reachable closure, not merely a closed superset of pins.
    pending = {"mineagent_workflow_checkpoint", "mineagent_workflow_consistent"}
    pending.update(
        re.findall(
            r"\b(mineagent_\w+)\s*\(", diag.PREFIX + "\n".join(diag.CHECKS.values())
        )
    )
    reached = set()
    while pending:
        name = pending.pop()
        if name in reached:
            continue
        reached.add(name)
        pending.update(re.findall(r"\b(mineagent_\w+)\s*\(", definitions[name][1]))
    assert reached == diag.PINS.keys()
    return rows


class Connection:
    def __init__(self, rows=None, failure=None, value=True):
        self.rows = catalog() if rows is None else rows
        self.failure, self.value = failure, value
        self.opened = self.rolled_back = self.calls = 0

    def begin_nested(self):
        self.opened += 1

        def rollback():
            self.rolled_back += 1

        return NS(rollback=rollback)

    def execute(self, query, params=None):
        self.calls += 1
        query = str(query)
        assert query.startswith(("SELECT", "WITH"))
        if params is not None and "names" not in params:
            assert set(params) == {"owner", "workflow", "checkpoint"}
            assert all(str(value) not in query for value in params.values())
        if self.failure == self.calls:
            error = RuntimeError(PRIVATE)
            error.orig = NS(sqlstate="42703", message_detail=PRIVATE)
            raise error
        return NS(all=lambda: self.rows, scalar_one=lambda: self.value)


def test_exact_pins_and_every_fixed_probe_rolls_back_without_committing():
    conn = Connection()
    result = diag.probe(conn, uuid4(), uuid4(), uuid4())
    assert result["category"] == "observed"
    assert {r["category"] for r in result["checks"]} == {
        "root_predicate",
        "checkpoint_predicate",
        *diag.CHECKS,
    }
    assert len(result["checks"]) == 11
    assert all(row["value"] is True for row in result["checks"])
    assert conn.opened == conn.rolled_back == conn.calls == 12

    # The new helper is part of the immutable catalog-only closure. Neither old
    # schema/raw bodies nor an unknown helper alias may be treated as current.
    rows = catalog()
    helper = "mineagent_rd_schema_recursive_032e"
    assert diag.PINS[helper][1:] == ("i", "catalog")
    predecessor = frozen_definitions("032e_rd_workflow_cost")
    assert helper not in predecessor
    for name in ("mineagent_rd_schema", "mineagent_structured_raw_json"):
        stale = [
            (row[0], predecessor[name][1], *row[2:]) if row[0] == name else row
            for row in rows
        ]
        old = Connection(rows=stale)
        assert diag.probe(old, uuid4(), uuid4(), uuid4()) == {
            "category": "installed_body_mismatch"
        }
        assert old.calls == old.rolled_back == 1
    unknown = [
        (row[0] + "_future", *row[1:]) if row[0] == helper else row for row in rows
    ]
    changed = Connection(rows=unknown)
    assert diag.probe(changed, uuid4(), uuid4(), uuid4()) == {
        "category": "installed_body_mismatch"
    }
    assert changed.calls == changed.rolled_back == 1


@pytest.mark.parametrize("failure", range(1, 13))
def test_every_query_error_is_sanitized_and_savepoint_rolled_back(failure):
    conn = Connection(failure=failure)
    result = diag.probe(conn, uuid4(), uuid4(), uuid4())
    assert PRIVATE not in str(result)
    assert "42703" in str(result)
    assert conn.opened == conn.rolled_back == conn.calls
    assert conn.calls == (1 if failure == 1 else 12)


@pytest.mark.parametrize(
    "change", ["body", "security", "volatility", "setting", "duplicate", "missing"]
)
def test_unknown_or_changed_installed_bodies_are_not_executed(change):
    source = catalog()
    targets = {
        source[0][0],
        "mineagent_rd_schema",
        "mineagent_structured_raw_json",
        "mineagent_rd_schema_recursive_032e",
    }
    for target in targets:
        rows = list(source)
        index = next(i for i, row in enumerate(rows) if row[0] == target)
        if change == "missing":
            rows.pop(index)
        elif change == "duplicate":
            rows.append(rows[index])
        else:
            row = list(rows[index])
            row[{"body": 1, "security": 2, "volatility": 3, "setting": 4}[change]] = {
                "body": PRIVATE,
                "security": True,
                "volatility": "s",
                "setting": "other",
            }[change]
            rows[index] = tuple(row)
        conn = Connection(rows=rows)
        assert diag.probe(conn, uuid4(), uuid4(), uuid4()) == {
            "category": "installed_body_mismatch"
        }
        assert conn.calls == conn.rolled_back == 1


@pytest.mark.parametrize("value", [None, PRIVATE, 0, 1, {}, []])
def test_non_boolean_values_are_unknown_never_raw(value):
    result = diag.probe(Connection(value=value), uuid4(), uuid4(), uuid4())
    assert all(row["value"] == "unknown" for row in result["checks"])
    assert PRIVATE not in str(result)


def test_untrusted_identifiers_and_sqlstates_are_not_echoed():
    conn = Connection()
    assert diag.probe(conn, PRIVATE, uuid4(), uuid4()) == {
        "category": "invalid_identity"
    }
    assert conn.calls == 0
    assert diag.sqlstate(NS(orig=NS(sqlstate=PRIVATE))) == "other"
    assert diag.sqlstate(NS(orig=NS(sqlstate=[]))) == "other"


@pytest.mark.parametrize("failure", [False, True])
def test_observer_preserves_original_return_error_and_restores_wrapper(
    monkeypatch, capsys, failure
):
    from backend.app import research_workflow_rd_checkpoint as service

    error = RuntimeError(PRIVATE)
    result = (NS(id=uuid4()), object())
    calls = []

    def original(*args, **kwargs):
        calls.append((args, kwargs))
        if failure:
            raise error
        return result

    monkeypatch.setattr(service, "observe_rd_terminal", original)
    conn = Connection()
    session = NS(connection=lambda: conn)
    with diag.diagnose_workflow_checkpoint():
        if failure:
            with pytest.raises(RuntimeError) as caught:
                service.observe_rd_terminal(session, uuid4(), uuid4(), "unchanged")
            assert caught.value is error
        else:
            assert (
                service.observe_rd_terminal(session, uuid4(), uuid4(), "unchanged")
                is result
            )
    assert service.observe_rd_terminal is original and len(calls) == 1
    assert PRIVATE not in capsys.readouterr().out
    assert conn.calls == (0 if failure else 12)


@pytest.mark.parametrize("failure", [1, 2, 12])
def test_baseexception_still_rolls_back_open_savepoint(failure):
    conn = Connection()
    original = conn.execute

    def interrupted(*args, **kwargs):
        if conn.calls + 1 == failure:
            conn.calls += 1
            raise KeyboardInterrupt
        return original(*args, **kwargs)

    conn.execute = interrupted
    with pytest.raises(KeyboardInterrupt):
        diag.probe(conn, uuid4(), uuid4(), uuid4())
    assert conn.opened == conn.rolled_back == failure


def test_observer_internal_failure_does_not_mask_original_commit_error(
    monkeypatch, capsys
):
    from backend.app import research_workflow_rd_checkpoint as service

    result = (NS(id=uuid4()), object())
    original = lambda *a, **kw: result
    monkeypatch.setattr(service, "observe_rd_terminal", original)

    def broken(*a):
        raise RuntimeError(PRIVATE)

    monkeypatch.setattr(diag, "probe", broken)
    error = ValueError("original commit failure")
    with pytest.raises(ValueError) as caught, diag.diagnose_workflow_checkpoint():
        assert (
            service.observe_rd_terminal(NS(connection=lambda: None), uuid4(), uuid4())
            is result
        )
        raise error
    assert caught.value is error and service.observe_rd_terminal is original
    output = capsys.readouterr().out
    assert PRIVATE not in output and "observer_error" in output
