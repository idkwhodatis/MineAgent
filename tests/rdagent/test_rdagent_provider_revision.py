"""026 literal composition checks; no substitute for actual PostgreSQL gates."""

import ast
from pathlib import Path

from backend.app import rdagent_provider_revision as revision
from script.generate_rd_seed_migration import document


def test_frozen_migration_is_exact_and_has_separated_table_statements():
    path = Path("backend/alembic/versions/026_rd_seeded_submissions.py")
    assert path.read_text() == document()
    module = ast.parse(path.read_text())
    values = {
        n.targets[0].id: ast.literal_eval(n.value)
        for n in module.body
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
    }
    table = values["TABLE_SQL"]
    assert table.count("CREATE TABLE ") == 4
    assert ");\n\nCREATE TABLE" in table
    assert table.count(";") >= 4
    assert "no_parameters" in path.read_text()
    assert 'execution_options={"no_parameters": True}' in path.read_text()


def test_versioned_provider_preserves_fixture_and_oe_protocols():
    from backend.app.rdagent_seed_revision import bootstrap_delta_sql

    sql = revision.render_upgrade_sql() + bootstrap_delta_sql()
    for protocol in (
        "mineagent.rdagent-chat.v1",
        "mineagent.rdagent-provider-chat.v1",
        "mineagent.rdagent-provider-embedding.v1",
        "openevolve",
    ):
        assert protocol in sql
    assert "mineagent_rd_fixture_config_026" in sql
    assert "mo.transport_id=mt.id" in sql
    assert "mo.operation_id" not in sql
    assert "phase='knowledge_update' AND kind='embedding'" in sql
    assert (
        "parser_profile IN ('mineagent.rdagent-source-parser.v1','mineagent.rdagent-provider-source-parser.v1')"
        in sql
    )


def test_provider_full_hold_is_not_fake_measured_billing():
    sql = revision.neutral_model_sql()
    assert "s.outcome IS DISTINCT FROM 'conservative_unknown'" in sql
    assert "s.charge IS DISTINCT FROM r.amounts" in sql
    assert (
        "((o.outcome='unknown') IS DISTINCT FROM (s.outcome='conservative_unknown'))"
        in sql
    )
    down = revision.render_downgrade_sql()
    assert "Configured provider or steer declarations prevent downgrade" in down
    assert "WHERE config->>'schema_version'='mineagent.factor-research.v2'" in down
