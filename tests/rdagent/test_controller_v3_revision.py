"""Frozen027 delta checks; PostgreSQL execution is a separate acceptance gate."""

import ast
from pathlib import Path

import pytest

from backend.app import rdagent_controller_v3_revision as m
from script.generate_rd_controller_v3_migration import document

ROOT = Path(__file__).resolve().parents[2]


def test_literal_exact_and_old_history_reproducible():
    assert (
        document()
        == (ROOT / "backend/alembic/versions/027_rd_controller_v3.py").read_text()
    )
    from script.generate_rd_seed_migration import document as old_document

    assert (
        old_document()
        == (ROOT / "backend/alembic/versions/026_rd_seeded_submissions.py").read_text()
    )
    tree = ast.parse(document())
    imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert all(
        not isinstance(n, ast.ImportFrom) or n.module == "alembic" for n in imports
    )


def test_all_prior_functions_restored_byte_exact_except_replace_spelling():
    down = m.render_downgrade_sql()
    for name in m.SOURCES:
        assert m.frozen(name) in down
    assert down.index("downgrade refused") < down.index("DROP TRIGGER")
    assert m.previous_gate() in down
    assert "CASCADE" not in down


def test_source_hash_pin_fails_closed(monkeypatch):
    monkeypatch.setitem(
        m.PINS, ("026_rd_seeded_submissions.py", "UPGRADE_SQL"), "0" * 64
    )
    with pytest.raises(ValueError, match="Frozen controller predecessor changed"):
        m.render_upgrade_sql()


def test_new_profile_only_fixed_controller_limits_and_same_physical_guards():
    sql = m.render_upgrade_sql()
    assert "THEN 120000 ELSE 60000 END)" in sql
    assert "THEN interval '120 seconds' ELSE interval '60 seconds' END)" in sql
    assert "THEN 300000 ELSE 240000 END)+32*" in sql
    assert "max_iterations" in m.PROFILE_SQL
    assert "IF NOT (v ? 'controller_profile')" in m.PROFILE_SQL
    assert (
        "mineagent_rd_provider_config_026_frozen(v-'controller_profile')"
        in m.PROFILE_SQL
    )
    for guard in (
        "Controller positive stop metadata differs",
        "Exact trusted child/peer/NSpid identity differs",
        "Controller settlement requires stopped full conservative charge",
        "ab.resumed_checkpoint_id IS NOT NULL",
        "67174400",
        "Protected leader physical identity differs",
    ):
        assert guard in sql
    assert (
        "mineagent_rd_controller_profile_matches(r.config,a.profile) IS NOT TRUE"
        in m.BINDING_SQL
    )
    assert m.PROFILE_SQL.count("mineagent_rd_json_bytes(v,true)<=1048576") == 2


def test_runtime_hash_adds_only_explicit_controller_selection():
    sql = m.render_upgrade_sql()
    assert (
        sql.count(
            "p.qlib_installed_parent_image_id) || mineagent_rd_controller_selection(r.config))"
        )
        == 4
    )
    assert "WHEN NOT (v ? 'controller_profile') THEN '{}'::jsonb" in m.PROFILE_SQL
    assert "ELSE NULL END" in m.PROFILE_SQL
