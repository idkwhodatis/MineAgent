"""Explicit026 deltas over pinned historical literals, not regenerated old ORM.

Only the new seed table is rendered from current metadata. The old launch table's
historical CHECK is replaced explicitly here; no018/020 literal is rewritten.
The assembler must freeze these strings into026 before any schema activation.
"""

import ast
import hashlib
from pathlib import Path

FROZEN_020_UPGRADE_SHA256 = (
    "1557ba0d6c69adbb461b0d152c8fac938b51d9d97a2a70936860a6f63f447b34"
)
BOOTSTRAP_CHECK = "ec_6ffe30774943edd35e0aa6bb"


def _literal(revision, name):
    module = ast.parse(
        (Path(__file__).parents[1] / "alembic" / "versions" / revision).read_text()
    )
    return next(
        ast.literal_eval(node.value)
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        )
    )


def _function(sql, name):
    markers = (
        "CREATE FUNCTION " + name + "(",
        "CREATE OR REPLACE FUNCTION " + name + "(",
    )
    matches = [sql.index(marker) for marker in markers if marker in sql]
    if len(matches) != 1:
        raise ValueError("Exact historical function required")
    start = position = matches[0]
    end = sql.index("END $$;", position) + len("END $$;")
    return sql[start:end].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


def previous_shared_sql():
    # 025 retains the 024 OE-aware shared predicate. Restoring020 here would
    # silently erase the already-installed OpenEvolve protocol/context branch.
    from backend.app.rdagent_provider_revision import function, literal

    return function(
        literal("024_openevolve_initial.py"), "mineagent_rd_shared_consistent"
    )


def _bootstrap_version_sql(*, expected, spec, admission):
    """Same four real producer grammars in both independent installed guards."""
    return rf"""
  IF {spec}.config->>'schema_version'='mineagent.factor-research.v2' THEN
   {expected}:={expected} || jsonb_build_object('schema_version','mineagent.rdagent-provider-operational-bootstrap.v1');
   IF {admission}.profile IS DISTINCT FROM 'mineagent.rdagent-orchestrator.v2'
   THEN RAISE EXCEPTION 'Configured provider controller requires protected exact profile'; END IF;
  END IF;
  IF EXISTS(SELECT 1 FROM factor_seeded_submissions WHERE run_id=cl.run_id) THEN
   SELECT {expected} || jsonb_build_object(
    'schema_version',(CASE WHEN {spec}.config->>'schema_version'='mineagent.factor-research.v2' THEN 'mineagent.rdagent-provider-seeded-bootstrap.v1' ELSE 'mineagent.rdagent-seeded-operational-bootstrap.v1' END),
    'continuation','seed_parent_first_generation','seeded_submission_id',s.id,
    'seed',jsonb_build_object('reference',mineagent_rd_seed_reference(s.id),
      'definition',v.definition,'source',v.code)) INTO {expected}
   FROM factor_seeded_submissions s JOIN factor_versions v ON v.id=s.parent_factor_version_id
   WHERE s.run_id=cl.run_id AND s.owner_id=cl.owner_id;
   IF {admission}.profile IS DISTINCT FROM 'mineagent.rdagent-orchestrator.v2'
   THEN RAISE EXCEPTION 'Seeded controller requires protected exact profile'; END IF;
  END IF;
"""


def bootstrap_delta_sql():
    sql = previous_shared_sql()
    from backend.app.rdagent_provider_revision import changed

    sql = changed(
        sql,
        "op.protocol NOT IN ('mineagent.rdagent-chat.v1','mineagent.rdagent-embedding.v1')",
        "(CASE WHEN rr.config->>'schema_version'='mineagent.factor-research.v2' "
        "THEN op.protocol NOT IN ('mineagent.rdagent-provider-chat.v1','mineagent.rdagent-provider-embedding.v1') "
        "ELSE op.protocol NOT IN ('mineagent.rdagent-chat.v1','mineagent.rdagent-embedding.v1') END)",
    )
    marker = "  IF ab.resumed_checkpoint_id IS NOT NULL OR cl.bootstrap IS DISTINCT FROM expected"
    if sql.count(marker) != 1:
        raise ValueError("Historical bootstrap predicate differs")
    extra = _bootstrap_version_sql(expected="expected", spec="spec", admission="pa")
    sql = sql.replace(marker, extra + marker)
    return (
        f"ALTER TABLE factor_controller_launches DROP CONSTRAINT {BOOTSTRAP_CHECK};\n"
        f"ALTER TABLE factor_controller_launches ADD CONSTRAINT {BOOTSTRAP_CHECK} CHECK ("
        "(jsonb_typeof(bootstrap)='object' AND bootstrap->>'schema_version' IN "
        "('mineagent.rdagent-operational-bootstrap.v1','mineagent.rdagent-seeded-operational-bootstrap.v1','mineagent.rdagent-provider-operational-bootstrap.v1','mineagent.rdagent-provider-seeded-bootstrap.v1') "
        "AND octet_length(bootstrap::text)<=262144) IS TRUE);\n"
        + sql
        + "\n"
        + controller_bootstrap_delta_sql()
    )


def previous_controller_sql():
    from backend.app.rdagent_provider_revision import function, literal

    return function(
        literal("024_openevolve_initial.py"), "mineagent_rd_controller_consistent"
    )


def controller_bootstrap_delta_sql():
    """Retain the entire024 physical chain; change only expected bootstrap bytes."""
    from backend.app.rdagent_provider_revision import changed

    old = previous_controller_sql()
    marker = (
        "  IF cl.bootstrap<>expected_bootstrap OR cl.bootstrap_sha256<>a.input_sha256"
    )
    extra = _bootstrap_version_sql(
        expected="expected_bootstrap", spec="sp", admission="a"
    )
    return changed(old, marker, extra + marker)


def bootstrap_restore_sql():
    return (
        f"ALTER TABLE factor_controller_launches DROP CONSTRAINT {BOOTSTRAP_CHECK};\n"
        f"ALTER TABLE factor_controller_launches ADD CONSTRAINT {BOOTSTRAP_CHECK} CHECK ("
        "(jsonb_typeof(bootstrap)='object' AND bootstrap->>'schema_version'="
        "'mineagent.rdagent-operational-bootstrap.v1' AND octet_length(bootstrap::text)<=262144) IS TRUE);\n"
        + previous_controller_sql()
        + "\n"
        + previous_shared_sql()
    )


FROZEN_023_UPGRADE_SHA256 = (
    "9cf5b775ea1faf0a1116bf825700d0a9a80c24425a0f18b98f2674f87eb56492"
)
SEED_TRIGGER_TABLES = (
    "factor_seeded_submissions",
    "factor_research_submissions",
    "research_runs",
    "factor_research_specs",
    "factor_analysis_plans",
    "outbox_events",
    "research_budget_controls",
    "factor_generation_starts",
    "factor_source_publications",
    "factor_versions",
    "factor_objects",
    "factors",
    "factor_controller_launches",
)


def previous_submission_sql():
    sql = _literal("023_rd_submissions.py", "UPGRADE_SQL")
    if hashlib.sha256(sql.encode()).hexdigest() != FROZEN_023_UPGRADE_SHA256:
        raise ValueError("Frozen023 source differs; review explicit successor")
    return _function(sql, "@SCHEMA@.mineagent_rd_submission_consistent")


def submission_delta_sql():
    sql = previous_submission_sql()
    old = "  AND NOT EXISTS(SELECT 1 FROM factor_research_submissions WHERE run_id=NEW.id)"
    if sql.count(old) != 1:
        raise ValueError("Historical queued-family predicate differs")
    # Exactly one family: neither missing nor simultaneous fresh+seed receipts.
    return sql.replace(
        old,
        """  AND (EXISTS(SELECT 1 FROM factor_research_submissions WHERE run_id=NEW.id))
   = (EXISTS(SELECT 1 FROM factor_seeded_submissions WHERE run_id=NEW.id))""",
    )


def render_upgrade_sql():
    from backend.app.rdagent_seed_sql import functions_sql

    return "\n".join(
        (
            functions_sql(),
            bootstrap_delta_sql(),
            submission_delta_sql(),
            "CREATE TRIGGER rd_seed_append BEFORE INSERT OR UPDATE OR DELETE ON @SCHEMA@.factor_seeded_submissions FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_rd_seed_append();",
            *(
                f"CREATE CONSTRAINT TRIGGER rd_seed_closed AFTER INSERT OR UPDATE OR DELETE ON @SCHEMA@.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION @SCHEMA@.mineagent_rd_seed_consistent();"
                for table in SEED_TRIGGER_TABLES
            ),
        )
    )


def render_downgrade_sql():
    # Even an unexecuted seed receipt has no truthful025 representation. Never
    # delete it, erase its parent, or reinterpret it as an old fresh submission.
    guard = """DO $$ BEGIN IF EXISTS(SELECT 1 FROM @SCHEMA@.factor_seeded_submissions)
 OR EXISTS(SELECT 1 FROM @SCHEMA@.factor_controller_launches WHERE bootstrap->>'schema_version'='mineagent.rdagent-seeded-operational-bootstrap.v1')
 THEN RAISE EXCEPTION 'Seeded declarations prevent downgrade'; END IF; END $$;"""
    return "\n".join(
        (
            guard,
            *(
                f"DROP TRIGGER rd_seed_closed ON @SCHEMA@.{table};"
                for table in SEED_TRIGGER_TABLES
            ),
            "DROP TRIGGER rd_seed_append ON @SCHEMA@.factor_seeded_submissions;",
            previous_submission_sql(),
            bootstrap_restore_sql(),
            "DROP FUNCTION @SCHEMA@.mineagent_rd_seed_consistent();",
            "DROP FUNCTION @SCHEMA@.mineagent_rd_seed_append();",
            "DROP FUNCTION @SCHEMA@.mineagent_rd_seed_check(uuid);",
            "DROP FUNCTION @SCHEMA@.mineagent_rd_seed_reference(uuid);",
            "DROP TABLE @SCHEMA@.factor_seeded_submissions;",
        )
    )
