"""026 literal composition checks; no substitute for actual PostgreSQL gates."""

import ast
import re
from pathlib import Path

from backend.app import rdagent_provider_revision as revision
from script.generate_rd_seed_migration import document


def record_alias_collisions(sql):
    """Static PL/pgSQL namespace regression, not a PostgreSQL execution proof."""
    found = []
    for match in re.finditer(
        r"CREATE(?: OR REPLACE)? FUNCTION ([^\s(]+).*?\$\$(.*?)\$\$;",
        sql,
        re.DOTALL,
    ):
        body = match.group(2)
        declaration = re.search(r"\bDECLARE\b(.*?)\bBEGIN\b", body, re.DOTALL)
        if declaration is None:
            continue
        variables = set(re.findall(r"(?:^|;)\s*(\w+)\s", declaration.group(1)))
        aliases = set(
            re.findall(
                r"\b(?:FROM|JOIN)\s+[\w.@]+\s+(?:AS\s+)?(\w+)",
                body,
                re.IGNORECASE,
            )
        )
        if variables & aliases:
            found.append((match.group(1), variables & aliases))
    return found


def test_frozen_migration_is_exact_and_has_separated_table_statements():
    path = Path("backend/alembic/versions/026_rd_seeded_submissions.py")
    assert path.read_text() == document()
    # All installed predecessors and downgrade restorations must avoid naming
    # a SQL table alias after a PL/pgSQL record, including zero-iteration paths.
    for prefix in ("020", "024", "026"):
        source = next(path.parent.glob(prefix + "_*.py"))
        for node in ast.parse(source.read_text()).body:
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                assert not record_alias_collisions(node.value.value), source.name
    for old_alias, new_alias in (
        ("a", "child_admission"),
        ("p", "child_receipt"),
        ("o", "boundary_observation"),
        ("t", "sdk_transport"),
        ("op", "sdk_operation"),
        ("held", "object_hold"),
    ):
        sql = document()
        # Decode the actual frozen SQL, not its Python-escaped source spelling.
        tree = ast.parse(sql)
        actual = next(
            ast.literal_eval(n.value)
            for n in tree.body
            if isinstance(n, ast.Assign) and n.targets[0].id == "UPGRADE_SQL"
        )
        damaged = re.sub(r"\b" + new_alias + r"\b", old_alias, actual)
        assert damaged != actual and record_alias_collisions(damaged)
    module = ast.parse(path.read_text())
    values = {
        n.targets[0].id: ast.literal_eval(n.value)
        for n in module.body
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
    }
    # Boolean support is an explicit026 successor, not a changed018 interpreter.
    original = revision.function(
        revision.literal("018_rd_source_persistence.py"), "mineagent_rd_schema"
    )
    boolean = revision.schema_boolean_sql()
    addition = " WHEN 'boolean' THEN NULL; -- Exact jsonb_typeof check above remains mandatory.\n"
    assert original.count(" WHEN 'null' THEN NULL;") == 1
    assert " WHEN 'boolean'" not in original
    assert boolean.count(addition) == 1
    assert boolean.replace(addition, "", 1) == original
    assert "IF jsonb_typeof(v) IS DISTINCT FROM" in boolean
    assert original in values["DOWNGRADE_SQL"]
    assert boolean in values["UPGRADE_SQL"]
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
