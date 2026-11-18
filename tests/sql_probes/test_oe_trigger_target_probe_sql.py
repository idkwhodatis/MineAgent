"""Offline event-local routing proof, not PostgreSQL/native acceptance."""

import ast
import hashlib
import re
from pathlib import Path

import pytest

from backend.app import oe_trigger_target_probe_sql as m
from backend.app import row_validation_oe_032g_base_sql as base

NAME = "mineagent_oes_consistent"
DECLARATION = "DECLARE versions jsonb[]; v jsonb; ids uuid[]; target uuid; rid uuid;"
GRAPH = "   PERFORM @SCHEMA@.mineagent_oes_graph_check(target);"
# SQL ANY may be NULL for unexpected NULL targets or array members.
# IS NOT TRUE preserves validation in that case instead of skipping later targets.
GUARDED_GRAPH = """   IF (target=ANY(seen)) IS NOT TRUE THEN
    PERFORM @SCHEMA@.mineagent_oes_graph_check(target);
    seen:=array_append(seen,target);
   END IF;"""


def frozen_trigger():
    sql = base.literal("031_workflow_structured_stage.py")
    assert hashlib.sha256(sql.encode()).hexdigest() == (
        "67c916d766057c38db8f9e275de1936dcc3edf7a9803931dff597ed6b912ec84"
    )
    matches = list(
        re.finditer(
            r"CREATE(?: OR REPLACE)? FUNCTION (?:@SCHEMA@\.)?" + NAME + r"\(\)", sql
        )
    )
    assert len(matches) == 1
    start = matches[0].start()
    return sql[start : sql.index("END $$;", start) + len("END $$;")]


def test_exactly_two_edits_to_frozen_latest_trigger():
    assert m.PREDECESSOR == "032g_schema_keyset_cost"
    assert m.CHANGED == (NAME,)
    old = frozen_trigger()
    assert m.original(NAME) == old
    assert old.count(DECLARATION) == old.count(GRAPH) == 1
    expected = old.replace(
        DECLARATION, DECLARATION + " seen uuid[]:=ARRAY[]::uuid[];", 1
    ).replace(GRAPH, GUARDED_GRAPH, 1)
    assert m.candidate(NAME) == expected
    with pytest.raises(ValueError):
        m.candidate("mineagent_oes_graph_check")


def test_old_new_reverse_membership_isolation_and_locks_unchanged():
    old, new = m.original(NAME), m.candidate(NAME)
    for marker in (
        "versions:=",
        "WHEN TG_OP=",
        "FOREACH ",
        "rid:=",
        "SELECT ",
        "WHERE ",
        "ORDER BY ",
        "cardinality(ids)",
        "IF NOT EXISTS",
        "AND NOT EXISTS",
        "current_setting",
        "RAISE EXCEPTION",
        "FOR UPDATE NOWAIT",
    ):
        assert [line for line in old.splitlines() if marker in line] == [
            line for line in new.splitlines() if marker in line
        ]
    assert "ARRAY[to_jsonb(OLD),to_jsonb(NEW)]" in new
    assert "LIMIT 10001" in new and "cardinality(ids)>10000" in new
    assert new.index("FOR UPDATE NOWAIT;") < new.index(
        "IF (target=ANY(seen)) IS NOT TRUE"
    )
    assert new.index(GRAPH.strip()) < new.index("seen:=array_append(seen,target);")
    assert new.count("seen") == 4
    assert "seen" not in new.split("BEGIN", 1)[1].split("FOREACH v", 1)[0]
    assert "SET CONSTRAINTS" not in new
    assert not re.search(r"\b(?:INSERT|UPDATE|DELETE)\s+(?:INTO|FROM|@SCHEMA@)", new)


def test_upgrade_and_restore_change_only_one_function_and_never_schema_version():
    for direction, definition in (
        ("upgrade", m.candidate(NAME)),
        ("downgrade", m.original(NAME)),
    ):
        sql = m.render_candidate_sql(direction)
        expected = re.sub(
            r"^CREATE(?: OR REPLACE)? FUNCTION",
            "CREATE OR REPLACE FUNCTION",
            definition,
        )
        assert expected in sql
        assert sql.count("CREATE OR REPLACE FUNCTION") == 1
        assert m.render_preflight_sql(direction) in sql
        assert not re.search(r"\b(?:CREATE|DROP)\s+(?:CONSTRAINT\s+)?TRIGGER\b", sql)
        assert not re.search(
            r"\b(?:INSERT INTO|UPDATE|DELETE FROM)\s+(?:@SCHEMA@\.)?alembic_version\b",
            sql,
        )
        assert "SECURITY DEFINER" not in sql and "DISABLE TRIGGER" not in sql
    with pytest.raises(ValueError):
        m.render_candidate_sql("invalid")


def test_frozen_source_tampering_is_rejected(monkeypatch, tmp_path):
    source = base.VERSIONS / "031_workflow_structured_stage.py"
    (tmp_path / source.name).write_text(
        source.read_text() + "\nUPGRADE_SQL = 'changed'\n"
    )
    monkeypatch.setattr(base, "VERSIONS", tmp_path)
    with pytest.raises(ValueError, match="Exact reviewed frozen literal"):
        m.original(NAME)


def test_probe_is_not_imported_by_runtime_or_migrations_and_cannot_execute_sql():
    module_path = Path(m.__file__).resolve()
    tree = ast.parse(module_path.read_text())
    assert not any(
        isinstance(node, ast.Attribute)
        and node.attr in {"execute", "exec_driver_sql", "commit"}
        for node in ast.walk(tree)
    )
    backend = module_path.parents[1]
    for directory in (backend / "app", backend / "alembic" / "versions"):
        for path in directory.rglob("*.py"):
            if path.resolve() == module_path:
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    assert all(
                        "oe_trigger_target_probe_sql" not in alias.name
                        for alias in node.names
                    ), path
                elif isinstance(node, ast.ImportFrom):
                    assert "oe_trigger_target_probe_sql" not in (node.module or ""), (
                        path
                    )
                    assert all(
                        alias.name != "oe_trigger_target_probe_sql"
                        for alias in node.names
                    ), path


def test_preflight_pins_current_schema_trigger_and_unchanged_function_closure():
    for direction in ("upgrade", "downgrade"):
        guard = m.render_preflight_sql(direction)
        assert "ARRAY['032g_schema_keyset_cost']" in guard
        assert "BETWEEN 180000 AND 189999" in guard
        assert "server_encoding')<>'UTF8'" in guard
        for name in base.SOURCES:
            assert base.function_assertion(base.original(name)) in guard
        trigger = m.candidate(NAME) if direction == "downgrade" else m.original(NAME)
        assert base.function_assertion(trigger) in guard
    with pytest.raises(ValueError):
        m.render_preflight_sql("invalid")
