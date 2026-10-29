"""032 metadata and emitted SQL boundaries; not physical normal OE acceptance."""

import ast
from pathlib import Path

from backend.app.normal_oe_note_models import NormalOENotePin
from backend.app.normal_oe_note_revision import (
    kind_sql,
    render_downgrade_sql,
    render_table_sql,
    render_upgrade_sql,
)
from script.generate_normal_oe_note_migration import document

ROOT = Path(__file__).resolve().parents[2]


def test_exact_attachment_fields_and_restrict_foreign_keys():
    table = NormalOENotePin.__table__
    assert set(table.c.keys()) == {
        "pin_id",
        "owner_id",
        "run_id",
        "attempt_id",
        "generation",
        "execution_id",
        "engine_outcome_id",
        "factor_exposure_id",
        "market_exposure_id",
    }
    assert [c.name for c in table.primary_key] == ["pin_id"]
    assert all(not c.nullable for c in table.c)
    assert len(table.foreign_key_constraints) == 6
    assert all(f.ondelete == "RESTRICT" for f in table.foreign_key_constraints)


def test_normal_kind_only_and_legacy_trigger_never_replaced():
    sql = render_upgrade_sql()
    assert kind_sql(True).replace(",'normal_oe_execution'", "") == kind_sql(False)
    assert "CREATE OR REPLACE" not in sql
    assert "FUNCTION validate_research_note_pin" not in sql
    assert "FUNCTION @SCHEMA@.validate_research_note_pin" not in sql
    assert sql.count("CREATE CONSTRAINT TRIGGER normal_note_consistent") == 3
    assert "BEFORE UPDATE OR DELETE" in sql
    assert "reject_research_note_change" in sql


def test_downgrade_refuses_every_new_history_shape():
    sql = render_downgrade_sql()
    assert "research_note_normal_oe_pins)" in sql
    assert "resource_kind='normal_oe_execution'" in sql
    assert "normal_oe_decision" in sql and "normal_oe_fill" in sql
    assert sql.index("history prevents downgrade") < sql.index("DROP TRIGGER")
    assert sql.endswith(kind_sql(False))


def test_literal_migration_matches_exact_current_generator():
    path = ROOT / "backend/alembic/versions/032_normal_oe_note_pins.py"
    assert path.read_text() == document()
    tree = ast.parse(path.read_text())
    vals = {
        n.targets[0].id: ast.literal_eval(n.value)
        for n in tree.body
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
    }
    assert vals["revision"] == "032_normal_oe_note_pins"
    assert vals["down_revision"] == "031_workflow_structured_stage"
    assert vals["TABLE_SQL"] == render_table_sql()
    assert vals["UPGRADE_SQL"] == render_upgrade_sql()
    assert vals["DOWNGRADE_SQL"] == render_downgrade_sql()
    assert {
        "research_note_revisions",
        "research_note_evidence_pins",
        "data_usage_exposures",
    } <= set(vals["EXISTING_TABLES"])


def test_identity_normalization_is_explicit_locale_independent_and_local():
    from backend.app.normal_oe_note_identity_sql import render_identity_sql

    sql = render_identity_sql()
    assert "mapping constant jsonb" in sql
    assert 'COLLATE "C"' in sql
    assert "upper(" not in sql.replace("mineagent_normal_note_upper(", "")
    assert "CREATE OR REPLACE" not in sql
    assert (
        "x.identities IS DISTINCT FROM mineagent_normal_note_dataset_identities(d.id)"
        in render_upgrade_sql()
    )
