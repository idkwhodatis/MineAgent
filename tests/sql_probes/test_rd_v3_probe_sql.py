"""Offline scope/equivalence proofs, not real PostgreSQL or runtime acceptance."""

import ast
import re
from importlib import import_module
from pathlib import Path

import pytest

from backend.app import rd_v3_invariant_probe_sql as m
from backend.app import row_validation_invariant_sql as prior


def test_only_three_exact_single_predicate_edits():
    assert m.CHANGED == prior.CHANGED[:3]
    for name in m.CHANGED:
        before, after = m.edits(name)
        assert m.original(name).count(before) == 1
        assert m.candidate(name) == m.original(name).replace(before, after, 1)
        assert (
            "IN ('mineagent.factor-research.v2','mineagent.factor-research.v3')"
            not in after
        )
        assert "='mineagent.factor-research.v3'" in after
        assert "ELSE " in after  # Original validator remains outside exact v3 guard.
        for call in re.findall(r"mineagent_\w+\([^()]*\)", before):
            assert call in after
    with pytest.raises(ValueError):
        m.candidate(prior.CHANGED[3])


def test_graph_payload_and_relation_error_order_remain():
    for name in m.CHANGED:
        old, new = m.original(name), m.candidate(name)
        for marker in (
            "SELECT ",
            "PERFORM ",
            "RAISE ",
            "EXCEPTION ",
            "mineagent_rd_provider_payload(",
        ):
            assert [l for l in old.splitlines() if marker in l] == [
                l for l in new.splitlines() if marker in l
            ]
    assert (
        "AND mineagent_rd_provider_payload(op.request->>'kind',op.request) IS TRUE THEN"
        in m.candidate(m.CHANGED[0])
    )


def test_fresh_scan_precedes_shortcuts_inverse_on_restore():
    sql = m.render_candidate_sql()
    assert sql.count("ADD CONSTRAINT") == 1
    assert sql.index("ADD CONSTRAINT") < sql.index("CREATE OR REPLACE FUNCTION")
    assert "NOT VALID" not in sql and "DISABLE TRIGGER" not in sql
    assert m.EXPRESSION.endswith("IS TRUE")
    assert "ELSE true END) IS TRUE" in m.EXPRESSION
    inverse = m.render_candidate_sql("downgrade")
    assert inverse.rindex("CREATE OR REPLACE FUNCTION") < inverse.index(
        "DROP CONSTRAINT"
    )
    assert "UPDATE alembic_version" not in sql + inverse
    assert prior.render_maintenance_sql() in sql
    assert len(prior.maintenance_tables()) == 187


def test_current_keyset_and_unchanged_helpers_match_installed_contract():
    keyset = import_module("backend.alembic.versions.032g_schema_keyset_cost")
    assert m.original("mineagent_rd_schema") == keyset.UPGRADE_SQL
    for name in keyset.FUNCTION_SOURCES:
        if name != "mineagent_rd_schema":
            assert m.original(name) == keyset.FUNCTION_SOURCES[name]
    sql = m.render_preflight_sql()
    assert "ARRAY['032g_schema_keyset_cost']" in sql
    assert "UTF8 PostgreSQL18 probe required" in sql
    for name in prior.SOURCES:
        assert prior.function_assertion(m.original(name)) in sql


def test_changed_frozen_main_fails_closed(monkeypatch, tmp_path):
    original = (prior.VERSIONS / "032g_schema_keyset_cost.py").read_text()
    (tmp_path / "032g_schema_keyset_cost.py").write_text(
        original + "\nUPGRADE_SQL = 'changed'\n"
    )
    monkeypatch.setattr(prior, "VERSIONS", tmp_path)
    with pytest.raises(ValueError, match="Exact reviewed"):
        m.original("mineagent_rd_schema")


def test_immutable_body_and_enabled_trigger_are_pinned():
    old = import_module("backend.alembic.versions.014_run_identity")
    assert (
        old.input_protection(include_identity=True)
        .split("AS $$", 1)[1]
        .removesuffix("$$;")
        == m.IMMUTABLE_BODY
    )
    guard = m.render_preflight_sql()
    assert "tgname='immutable_research_inputs'" in guard
    assert "tgtype=19" in guard and "tgenabled IN ('O','A')" in guard
    assert m.IMMUTABLE_BODY in guard
    assert "Fresh RD probe invariant already exists" in guard
    assert "convalidated AND NOT condeferrable" in m.render_preflight_sql("downgrade")


def test_pure_closure_remains_immutable_and_without_relations():
    pending, found = ["mineagent_rd_workflow_config"], set()
    while pending:
        name = pending.pop()
        if name in found:
            continue
        found.add(name)
        header, body = m.original(name).split("AS $$", 1)
        assert " IMMUTABLE " in header
        body = re.sub(r"'(?:''|[^'])*'", "''", re.sub(r"--[^\n]*", "", body))
        assert not re.search(
            r"\b(INSERT|UPDATE|DELETE|EXECUTE|LOCK|PERFORM|nextval|set_config|random)\b",
            body,
            re.IGNORECASE,
        )
        for table in prior.maintenance_tables():
            assert not re.search(
                r"\b(?:FROM|JOIN)\s+(?:@SCHEMA@\.)?" + table + r"\b",
                body,
                re.IGNORECASE,
            )
        pending.extend(set(re.findall(r"\b(mineagent_\w+)\s*\(", body)) - found)
    assert found <= set(prior.PURE)
    assert "mineagent_rd_schema" in found


def test_no_runtime_migration_or_commit_installation():
    tree = ast.parse(Path(m.__file__).read_text())
    assert not any(
        isinstance(n, ast.Attribute)
        and n.attr in {"commit", "execute", "exec_driver_sql"}
        for n in ast.walk(tree)
    )
    with pytest.raises(ValueError):
        m.render_candidate_sql("invalid")
