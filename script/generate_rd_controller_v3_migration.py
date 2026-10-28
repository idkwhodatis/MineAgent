"""Freeze027 explicit controller deltas without rewriting published history."""

from pathlib import Path

from backend.app.rdagent_controller_v3_revision import (
    render_downgrade_sql,
    render_upgrade_sql,
)

ROOT = Path(__file__).resolve().parents[1]


def document():
    header = '"""Explicit fixed120 RD controller; no change to historical v2/60 semantics.\n\nLiteral-only migration, no application imports or engine execution.\n"""\n\nimport re\n\nfrom alembic import op\n\nrevision = "027_rd_controller_v3"\ndown_revision = "026_rd_seeded_submissions"\nbranch_labels = None\ndepends_on = None\n\n# fmt: off\n'
    for name, value in (
        ("UPGRADE_SQL", render_upgrade_sql()),
        ("DOWNGRADE_SQL", render_downgrade_sql()),
    ):
        header += "\n" + name + " = " + repr(value) + "\n"
    return (
        header
        + '\n# fmt: on\n\n\ndef _schema():\n    value = op.get_bind().exec_driver_sql("SELECT current_schema()").scalar_one()\n    if type(value) is not str or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value) is None:\n        raise RuntimeError("Exact installation schema required")\n    return \'"\' + value + \'"\'\n\n\ndef _maintenance(schema):\n    op.execute("""DO $$ BEGIN\n    IF current_setting(\'transaction_isolation\') <> \'read committed\'\n    OR NOT pg_try_advisory_xact_lock(1413960260,1129270340)\n    THEN RAISE EXCEPTION \'Controller version migration requires quiescent maintenance\'; END IF;\n    END $$;""")\n    tables = (\n        "research_runs",\n        "execution_attempts",\n        "research_process_admissions",\n        "research_process_states",\n        "research_process_receipts",\n        "research_budget_reservations",\n        "research_budget_settlements",\n        "research_budget_controls",\n        "factor_research_specs",\n        "factor_research_attempt_bindings",\n        "factor_controller_launches",\n        "factor_research_submissions",\n        "factor_seeded_submissions",\n        "research_model_operations",\n        "research_model_transports",\n        "research_model_outcomes",\n        "factor_generation_model_operations",\n        "factor_source_publications",\n        "factor_source_read_witnesses",\n    )\n    op.execute(\n        "LOCK TABLE "\n        + ",".join(schema + "." + name for name in tables)\n        + " IN ACCESS EXCLUSIVE MODE NOWAIT"\n    )\n    op.execute("SET LOCAL search_path = " + schema + ",pg_catalog")\n\n\ndef upgrade():\n    schema = _schema()\n    _maintenance(schema)\n    op.get_bind().exec_driver_sql(\n        UPGRADE_SQL.replace("@SCHEMA@", schema),\n        execution_options={"no_parameters": True},\n    )\n\n\ndef downgrade():\n    schema = _schema()\n    _maintenance(schema)\n    op.get_bind().exec_driver_sql(\n        DOWNGRADE_SQL.replace("@SCHEMA@", schema),\n        execution_options={"no_parameters": True},\n    )\n'
    )


if __name__ == "__main__":
    (ROOT / "backend/alembic/versions/027_rd_controller_v3.py").write_text(document())
