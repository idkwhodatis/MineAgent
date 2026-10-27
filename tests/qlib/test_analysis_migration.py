"""Frozen022 offline inventory checks, not execution of SQL or runtime gates."""

import ast
from pathlib import Path

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
    assert values["RAW_SQL"] == RAW_SQL
    assert values["PLAN_SQL"] == PLAN_SQL
    assert values["ANALYSIS_SQL"] == ANALYSIS_SQL + CONSISTENCY_SQL
    assert values["CONTRACT_SQL"] == render_contract_sql()
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
