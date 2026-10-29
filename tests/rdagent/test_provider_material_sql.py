"""027b reuses pure captured values, never relational or process evidence."""

import ast
import hashlib
from pathlib import Path

from backend.app.rdagent_provider_material_sql import (
    BINDING_SQL,
    MATERIAL_SQL,
    previous_graph,
    render_upgrade_sql,
)

ROOT = Path(__file__).resolve().parents[2]


def literal(name):
    tree = ast.parse(
        (ROOT / "backend/alembic/versions/027b_provider_material.py").read_text()
    )
    return next(
        ast.literal_eval(n.value)
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)
    )


def test_literal_revision_is_exact_renderer_and_downgrade_restores_old_function():
    assert literal("revision") == "027b_provider_material"
    assert literal("down_revision") == "027a_contract_repairs"
    assert literal("UPGRADE_SQL") == render_upgrade_sql()
    assert literal("DOWNGRADE_SQL") == previous_graph()
    assert render_upgrade_sql().count("CREATE OR REPLACE FUNCTION") == 1
    assert "CREATE TABLE" not in render_upgrade_sql()
    assert "CREATE TRIGGER" not in render_upgrade_sql()

    # Preserve the preceding maintenance revision's exact lock/isolation scope.
    def maintenance_ast(filename):
        module = ast.parse((ROOT / "backend/alembic/versions" / filename).read_text())
        function = next(
            n
            for n in module.body
            if isinstance(n, ast.FunctionDef) and n.name == "_maintenance"
        )
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and "quiescent maintenance" in node.value
            ):
                node.value = node.value.replace(
                    "Provider material migration", "Contract repair migration"
                )
        return ast.dump(function, include_attributes=False)

    assert maintenance_ast("027b_provider_material.py") == maintenance_ast(
        "027a_contract_repairs.py"
    )


def test_all_original_relational_and_owner_guards_are_byte_identical():
    new = render_upgrade_sql()
    declarations = " binding jsonb; binding_valid boolean; binding_digest text; request_valid boolean; request_digest text; request_utf8 text; request_ascii text; output_tokens bigint; cost numeric;"
    new = (
        new.replace(declarations, "", 1)
        .replace(BINDING_SQL, "", 1)
        .replace(MATERIAL_SQL, "", 1)
    )
    new = new.replace(
        "request_valid IS NOT TRUE",
        "mineagent_rd_provider_request_binding(op.request,run.config->'model_binding') IS NOT TRUE",
        1,
    )
    new = new.replace(
        "op.request_sha256 IS DISTINCT FROM request_digest",
        "op.request_sha256 IS DISTINCT FROM encode(sha256(convert_to(mineagent_rd_canonical_json(op.request,false),'UTF8')),'hex')",
        1,
    )
    new = new.replace(
        "  IF amount IS NULL THEN",
        "  amount:=mineagent_rd_provider_reservation(op.request,run.config->'model_binding');\n  IF amount IS NULL THEN",
        1,
    )
    assert new == previous_graph()


def test_material_blocks_do_not_read_relations_or_cache_across_invocations():
    for block in (BINDING_SQL, MATERIAL_SQL):
        for forbidden in (
            "FROM research_",
            "FROM factor_",
            "FROM rd_",
            "pg_temp",
            "set_config",
            "FOR UPDATE",
            "CREATE",
            "INSERT",
            "UPDATE",
            "DELETE",
        ):
            assert forbidden not in block
    assert MATERIAL_SQL.count("mineagent_rd_canonical_json(op.request,false)") == 1
    assert MATERIAL_SQL.count("mineagent_rd_canonical_json(op.request,true)") == 1
    assert BINDING_SQL.count("mineagent_rd_provider_payload('binding',binding)") == 1
    assert "EXCEPTION WHEN OTHERS THEN amount:=NULL;" in MATERIAL_SQL
    assert (
        "EXCEPTION WHEN OTHERS THEN request_valid:=false; request_digest:=NULL;"
        in MATERIAL_SQL
    )


def test_historical_027_provider_graph_remains_pinned():
    assert (
        hashlib.sha256(previous_graph().encode()).hexdigest()
        == "2aab3ffe5662c10ee09190bdb5a0c5811768ef59fee68e8cb15c1beac156a181"
    )
