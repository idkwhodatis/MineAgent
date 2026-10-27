"""Frozen022 offline inventory checks, not execution of SQL or runtime gates."""

import ast
import re
from pathlib import Path

import pytest
from sqlalchemy import PrimaryKeyConstraint, UniqueConstraint

from backend.app.factor_analysis_canonical_sql import RAW_SQL
from backend.app.factor_analysis_models import (
    FactorAnalysisPlan,
    FactorDirectAnalysisIntent,
    FactorDirectAnalysisOutcome,
)
from backend.app.factor_analysis_sql import (
    ANALYSIS_SQL,
    CONSISTENCY_SQL,
    PLAN_SQL,
    render_contract_sql,
)
from backend.app.rdagent_configured_input_models import FactorConfiguredInputWitness

ROOT = Path(__file__).resolve().parents[2]


def literals():
    tree = ast.parse(
        (ROOT / "backend/alembic/versions/022_factor_analysis.py").read_text()
    )
    return {
        n.targets[0].id: ast.literal_eval(n.value)
        for n in tree.body
        if isinstance(n, ast.Assign)
        and len(n.targets) == 1
        and isinstance(n.targets[0], ast.Name)
    }


def test_frozen_analysis_sql_matches_reviewed_fragments():
    values = literals()
    assert values["revision"] == "022_factor_analysis"
    assert values["down_revision"] == "021_execution_snapshots"
    from backend.app.rdagent_configured_exposure_sql import (
        render_upgrade_sql as exposure_sql,
    )
    from backend.app.rdagent_configured_input_sql import render_upgrade_sql as input_sql

    assert values["EXPOSURE_UP"] == exposure_sql()
    assert values["INPUT_UP"] == input_sql()

    # Scan every string fragment, not only a chosen UPGRADE_SQL assignment:
    # 022 installs these two fragments independently of its other literals.
    def collisions(sql):
        found = []
        for match in re.finditer(
            r"CREATE(?: OR REPLACE)? FUNCTION ([^\s(]+).*?\$\$(.*?)\$\$;",
            sql,
            re.DOTALL | re.IGNORECASE,
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

    for migration in sorted((ROOT / "backend/alembic/versions").glob("*.py")):
        if migration.name[:3].isdigit() and int(migration.name[:3]) <= 26:
            for node in ast.walk(ast.parse(migration.read_text())):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    assert not collisions(node.value), migration.name
    for key, new, old in (
        ("EXPOSURE_UP", "exposure_plan", "p"),
        ("INPUT_UP", "cutoff_witness", "w"),
    ):
        damaged = re.sub(r"\b" + new + r"\b", old, values[key])
        assert damaged != values[key] and collisions(damaged)
    from backend.app.analysis_process_ddl import render_migration_sql, render_sql

    process_sql = values["ANALYSIS_PROCESS_SQL"]
    assert process_sql == render_migration_sql()
    assert process_sql.count("SET search_path=@SCHEMA@,pg_catalog") == 2
    assert render_sql("private_fixture") == process_sql.replace(
        "@SCHEMA@", "private_fixture"
    )
    # Scan headers, not merely bodies: an unqualified table lookup follows the
    # function's proconfig, not the caller's private installation schema.
    for migration in sorted((ROOT / "backend/alembic/versions").glob("*.py")):
        if migration.name[:3].isdigit() and int(migration.name[:3]) <= 26:
            for node in ast.walk(ast.parse(migration.read_text())):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    headers = re.findall(
                        r"CREATE(?: OR REPLACE)? FUNCTION\s+.*?AS \$\$",
                        node.value,
                        re.DOTALL,
                    )
                    for header in headers:
                        assert not re.search(r"search_path\s*=\s*public\b", header), (
                            migration.name
                        )
    # This exact broken conversion preserved qualified body references while
    # accidentally retaining public in both function-local path declarations.
    broken = render_sql().replace("public.", "@SCHEMA@.")
    assert broken.count("SET search_path=public,pg_catalog") == 2
    assert (
        broken.replace(
            "SET search_path=public,pg_catalog", "SET search_path=@SCHEMA@,pg_catalog"
        )
        == process_sql
    )
    assert values["RAW_SQL"] == RAW_SQL
    assert values["PLAN_SQL"] == PLAN_SQL
    assert values["ANALYSIS_SQL"] == ANALYSIS_SQL + CONSISTENCY_SQL
    assert values["CONTRACT_SQL"] == render_contract_sql()
    from backend.app.rdagent_controller_revision import frozen_source_sql
    from backend.app.rdagent_provider_revision import function
    from backend.app.rdagent_receipt_schema_sql import (
        NUMBER_BRANCH,
        receipt_contract_schema,
        receipt_schema_sql,
    )
    from packages.qlib_adapter.contracts import DirectResult
    from packages.qlib_adapter.objective import DirectObjectiveMeasurement
    from packages.research_runtime.analysis_process_journal import (
        AnalysisExtraction,
        AnalysisStop,
    )

    domain = receipt_schema_sql("analysis")
    restored = (
        domain.replace("mineagent_rd_analysis_schema", "mineagent_rd_schema")
        .replace(NUMBER_BRANCH, "")
        .replace(" WHEN 'boolean' THEN NULL;\n", "")
    )
    assert restored.replace(
        "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
    ) == function(frozen_source_sql(), "mineagent_rd_schema")
    assert "mineagent_rd_analysis_schema(v,rule" in values["CONTRACT_SQL"]
    assert "mineagent_rd_analysis_schema(v,rule" in values["ANALYSIS_PROCESS_SQL"]
    assert "analysis_uuid uuid" in values["ANALYSIS_SQL"]

    class DamagedSchema:
        @classmethod
        def model_json_schema(cls):
            return damaged

    for damaged in (
        {"type": "object", "additionalProperties": True},
        {"type": "object", "properties": {}},
        {"type": "string", "format": "unreviewed"},
        {"type": "string", "unevaluatedProperties": False},
    ):
        with pytest.raises(ValueError, match="Unreviewed|closed records"):
            receipt_contract_schema(DamagedSchema, "analysis")
    with pytest.raises(ValueError, match="Exact historical"):
        receipt_schema_sql("future")
    assert " id uuid;" not in values["ANALYSIS_SQL"]
    assert "WHERE id=a.logical_operation_id" in values["ANALYSIS_SQL"]
    for cls in (
        AnalysisStop,
        AnalysisExtraction,
        DirectResult,
        DirectObjectiveMeasurement,
    ):
        assert receipt_contract_schema(cls, "analysis")["additionalProperties"] is False
    assert (
        "mineagent_rd_analysis_schema(jsonb,jsonb,jsonb,integer)"
        in (ROOT / "backend/alembic/versions/022_factor_analysis.py").read_text()
    )
    assert "mineagent.qlib-direct-analysis.v1" in values["PROFILE_UP"]
    assert "mineagent.qlib-direct-analysis.v1" not in values["PROFILE_DOWN"]


def test_new_models_have_exact_fk_targets_and_no_legacy_snapshot_alias():
    for cls in (
        FactorAnalysisPlan,
        FactorDirectAnalysisIntent,
        FactorDirectAnalysisOutcome,
        FactorConfiguredInputWitness,
    ):
        for fk in cls.__table__.foreign_key_constraints:
            target = fk.elements[0].column.table
            fields = tuple(e.column.name for e in fk.elements)
            assert target.name != "feature_snapshots"
            assert any(
                isinstance(c, (PrimaryKeyConstraint, UniqueConstraint))
                and tuple(c.columns.keys()) == fields
                for c in target.constraints
            ), (cls.__name__, target.name, fields)


def test_migration_has_no_runtime_import_or_filesystem_sideeffects():
    source = (ROOT / "backend/alembic/versions/022_factor_analysis.py").read_text()
    assert "from backend.app" not in source
    assert "pg_try_advisory_xact_lock" in source
    assert "ACCESS EXCLUSIVE MODE NOWAIT" in source
    assert "Declared analysis evidence prevents downgrade" in source
