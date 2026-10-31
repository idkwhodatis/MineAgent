"""Offline diagnostic/privacy proof only; SQLite is NOT PG race acceptance."""

import ast
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError


def module():
    path = Path(__file__).parents[1] / "integration/submission_sql_diagnostic.py"
    spec = importlib.util.spec_from_file_location("submission_sql_probe", path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_closed_sqlstate_whitelist_never_formats_private_exception():
    diagnostic = module()

    class PrivateError:
        def __str__(self):
            raise AssertionError("must not render private error")

    for state, family in diagnostic.STATES.items():
        error = PrivateError()
        error.sqlstate = state
        assert diagnostic.classify(SimpleNamespace(original_exception=error)) == {
            "sqlstate": state,
            "family": family,
            **({"category": "other"} if state == "P0001" else {}),
        }
    for state in ("23505-private", "99999", "private", None, [], 23505):
        error = PrivateError()
        error.sqlstate = state
        assert diagnostic.classify(SimpleNamespace(original_exception=error)) == {
            "sqlstate": "other",
            "family": "other",
        }
    assert diagnostic.classify(object())["family"] == "other"
    assert (
        diagnostic.classify(
            SimpleNamespace(original_exception=SimpleNamespace(pgcode="40P01"))
        )["family"]
        == "deadlock"
    )


def test_actual_engine_listener_is_bounded_private_and_removed(capsys):
    diagnostic = module()
    engine = create_engine("sqlite://")
    other = create_engine("sqlite://")
    before = tuple(engine.dialect.dispatch.handle_error)
    try:
        with (
            pytest.raises(RuntimeError, match="unchanged"),
            diagnostic.diagnose_submission_sql(engine),
        ):
            for target, count in ((other, 1), (engine, 12)):
                for _ in range(count):
                    with (
                        target.connect() as connection,
                        pytest.raises(OperationalError),
                    ):
                        connection.exec_driver_sql(
                            "SELECT synthetic_private_missing_column"
                        )
            raise RuntimeError("unchanged")
        assert tuple(engine.dialect.dispatch.handle_error) == before
        output = capsys.readouterr().out
        assert "synthetic_private" not in output and "OperationalError" not in output
        report = json.loads(output.removeprefix("SUBMISSION_SQL_FAILURES:"))
        assert report == {
            "count": 12,
            "tail": [{"sqlstate": "other", "family": "other"}] * 8,
        }
        with engine.connect() as connection, pytest.raises(OperationalError):
            connection.exec_driver_sql("SELECT synthetic_private_missing_column")
        assert capsys.readouterr().out == ""
    finally:
        engine.dispose()
        other.dispose()


def test_concurrent_acceptance_keeps_exact_engine_and_conflict_assertion():
    source = (
        Path(__file__).parents[1] / "integration/test_strategy_versions.py"
    ).read_text()
    body = source.split(
        "def test_parallel_conflicting_submissions_cannot_overwrite_winner", 1
    )[1].split("\ndef test_", 1)[0]
    assert "diagnose_submission_sql(migrated_engine)" in body
    assert "== [201, 409]" in body
    assert "ThreadPoolExecutor(max_workers=2)" in body


def test_raised_categories_are_exact_frozen_literals_not_private_messages():
    diagnostic = module()
    root = Path(__file__).parents[2]
    frozen = []
    for path in (root / "backend/alembic/versions").glob("*.py"):
        if path.name[:3] <= "032":
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Constant) and type(node.value) is str:
                    frozen.append(node.value)
    for message, category in diagnostic.RAISED_CATEGORIES.items():
        assert any("RAISE EXCEPTION '" + message + "'" in sql for sql in frozen)
        for primary, expected in (
            (message, category),
            (message + " synthetic_private", "other"),
            ("synthetic_private " + message, "other"),
            ([], "other"),
        ):
            error = SimpleNamespace(
                sqlstate="P0001", diag=SimpleNamespace(message_primary=primary)
            )
            result = diagnostic.classify(SimpleNamespace(original_exception=error))
            assert result == {
                "sqlstate": "P0001",
                "family": "raised_exception",
                "category": expected,
            }
            assert "synthetic_private" not in json.dumps(result)


def test_oe_submission_exact_engine_observer_cleans_up_browser_failure(capsys):
    diagnostic = module()
    source = (
        Path(__file__).parents[1] / "integration/oe_structured_browser.py"
    ).read_text()
    node = next(
        n
        for n in ast.parse(source).body
        if isinstance(n, ast.FunctionDef)
        and n.name == "create_oe_submission_in_browser"
    )
    engine = create_engine("sqlite://")
    before = tuple(engine.dialect.dispatch.handle_error)
    request = SimpleNamespace(model_dump=lambda **_: {"synthetic_private": True})

    def browser(actual_engine, settings, account, other, observation):
        assert actual_engine is engine and other is None
        assert observation == {"mode": "submit", "request": {"synthetic_private": True}}
        with engine.connect() as connection, pytest.raises(OperationalError):
            connection.exec_driver_sql("SELECT synthetic_private_missing_column")
        raise RuntimeError("unchanged browser failure")

    namespace = {
        "_browser": browser,
        "diagnose_submission_sql": diagnostic.diagnose_submission_sql,
    }
    exec(  # noqa: S102 -- exact checked-in wrapper, no browser or external input
        compile(ast.Module(body=[node], type_ignores=[]), "exact_oe_wrapper", "exec"),
        namespace,
    )
    try:
        with pytest.raises(RuntimeError, match="unchanged browser failure"):
            namespace[node.name](engine, object(), object(), request)
        assert tuple(engine.dialect.dispatch.handle_error) == before
        output = capsys.readouterr().out
        assert "synthetic_private" not in output
        assert json.loads(output.removeprefix("SUBMISSION_SQL_FAILURES:")) == {
            "count": 1,
            "tail": [{"sqlstate": "other", "family": "other"}],
        }
        assert "create_app(settings=settings, engine=engine)" in source
    finally:
        engine.dispose()
