"""Wire-only normalization cannot mutate frozen history or weaken SQL failures."""

import hashlib
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest
from sqlalchemy import text

from backend.alembic import encoding_compat as compat

ROOT = Path(__file__).resolve().parents[2]


def frozen():
    return (ROOT / "backend/alembic/versions/001_m1.sql").read_text()


def test_frozen_source_exactly_one_comment_change():
    original = frozen()
    assert hashlib.sha256(original.encode()).hexdigest() == compat.FROZEN_SHA256
    with pytest.raises(UnicodeEncodeError):
        original.encode("latin-1")
    normalized = compat.latin1_m1_statement(original)
    assert normalized == original.replace(
        compat.COMMENT, compat.COMMENT.replace("—", "-")
    )
    assert normalized.encode("latin-1").decode("latin-1") == normalized
    assert sum(a != b for a, b in zip(original, normalized, strict=True)) == 1
    assert frozen() == original


@pytest.mark.parametrize(
    "mutation",
    [
        lambda s: s + "\n",
        lambda s: s.replace("CREATE TABLE users", "CREATE TABLE changed"),
        lambda s: s.replace("—", "-"),
    ],
)
def test_unrecognized_history_rejected(mutation):
    with pytest.raises(ValueError, match="Exact frozen001"):
        compat.latin1_m1_statement(mutation(frozen()))


def test_other_sql_unchanged_and_native_encoding_error_retained():
    sql = "SELECT '—'"
    assert compat.latin1_m1_statement(sql) == sql
    with pytest.raises(UnicodeEncodeError):
        compat.latin1_m1_statement(sql).encode("latin-1")


def test_sqlalchemy_hook_preserves_options_and_refuses_parameters():
    clause = text(frozen()).execution_options(example_option=True)
    new, multi, params = compat._before_execute(None, clause, (), {}, {})
    assert new.text == compat.latin1_m1_statement(clause.text)
    assert new.get_execution_options() == clause.get_execution_options()
    assert (multi, params) == ((), {})
    with pytest.raises(ValueError, match="bound SQL"):
        compat._before_execute(None, clause, (), {"unexpected": 1}, {})
    other = text("SELECT 1")
    assert compat._before_execute(None, other, (), {}, {})[0] is other


@pytest.mark.parametrize(
    "encoding,installed", [("iso8859-1", True), ("LATIN1", True), ("utf-8", False)]
)
def test_listener_scoped_and_removed_on_error(encoding, installed):
    connection = NS(
        dialect=NS(name="postgresql"),
        connection=NS(driver_connection=NS(info=NS(encoding=encoding))),
    )
    with (
        patch.object(compat.event, "listen") as listen,
        patch.object(compat.event, "remove") as remove,
    ):
        with (
            pytest.raises(RuntimeError, match="original failure"),
            compat.legacy_sql_encoding_compatibility(connection),
        ):
            raise RuntimeError("original failure")
        assert listen.call_count == remove.call_count == int(installed)
        if installed:
            listen.assert_called_once_with(
                connection, "before_execute", compat._before_execute, retval=True
            )
            remove.assert_called_once_with(
                connection, "before_execute", compat._before_execute
            )
