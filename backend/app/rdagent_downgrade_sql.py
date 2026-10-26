"""Fail-closed empty RD018 reversal, retaining legacy and registered017 numeric data.

Only renders SQL. Caller embeds reviewed output in the static Alembic revision
and executes it in one transaction. No CASCADE, file deletion, old validator
replacement, ORM reflection or guessed external dependency removal is used.
Historical anonymous checks recover their exact expressions, not a guarantee of
PostgreSQL's originally auto-generated names (018 did not retain those names).
"""

import re

from backend.app.numeric_process_ddl import PROFILE_GATE

DOMAIN_TABLES = (
    "factor_research_specs",
    "factor_generation_starts",
    "factor_upstream_calls",
    "factor_response_assemblies",
    "factor_generation_sources",
    "factor_evaluation_starts",
    "factor_cutoff_executions",
    "factor_candidate_input_inventories",
    "factor_candidate_input_files",
    "factor_generation_outcomes",
    "factor_research_checkpoints",
    "factor_research_attempt_bindings",
    "factor_controller_launches",
    "factor_cutoff_outcomes",
    "factor_evaluation_outcomes",
    "factor_generation_observations",
    "factor_generation_boundaries",
    "factor_generation_model_operations",
    "factor_response_assembly_parts",
    "factor_source_publications",
    "factor_source_publication_outcomes",
    "factor_source_read_witnesses",
    "factor_source_read_parts",
    "factor_upstream_call_outcomes",
)
EXTERNAL_UNIQUES = (
    (
        "factor_objects",
        "uq_engine_1deeb453b0f3abc32dda1374",
        ("owner_id", "id", "object_kind", "sha256"),
    ),
    (
        "factor_versions",
        "uq_engine_df15bd7f55fc6bd1f0337182",
        ("owner_id", "id", "code_sha256"),
    ),
    (
        "research_model_operations",
        "uq_engine_48d36f963874ee78496edf7c",
        ("owner_id", "run_id", "attempt_id", "generation", "id", "context_id"),
    ),
)
SHARED_TABLES = (
    "research_runs",
    "execution_attempts",
    "research_budget_controls",
    "research_budget_reservations",
    "research_budget_settlements",
    "research_operation_contexts",
    "research_context_aborts",
    "research_model_operations",
    "research_model_transports",
    "research_model_outcomes",
    "research_objects",
    "research_object_publications",
    "research_object_outcomes",
    "research_process_admissions",
    "research_process_receipts",
    "research_process_states",
    "factor_versions",
    "factor_objects",
    "engine_object_reservation_consumers",
)
FUNCTIONS = (
    "mineagent_rd_source_only_phase()",
    "mineagent_rd_source_deferred()",
    "mineagent_rd_source_violation(uuid)",
    "mineagent_rd_ascii_metadata(jsonb,integer)",
    "mineagent_rd_shared_consistent()",
    "mineagent_rd_source_run_lock()",
    "mineagent_engine_object_consistent()",
    "mineagent_engine_object_claim()",
    "mineagent_engine_evidence_immutable()",
    "mineagent_rd_source_config(jsonb)",
    "mineagent_rd_source_request(jsonb,text)",
    "mineagent_rd_source_context(jsonb)",
    "mineagent_rd_schema(jsonb,jsonb, jsonb,integer)",
    "mineagent_rd_json_bytes(jsonb,boolean,integer)",
    "mineagent_rd_canonical_json(jsonb,boolean,integer)",
)
OLD_CONTEXT = """(jsonb_typeof(context) = 'object' AND context ?& ARRAY['schema_version','scenario','ordinal']
 AND context - ARRAY['schema_version','scenario','ordinal'] = '{}'::jsonb
 AND context->>'schema_version' = 'mineagent.neutral-fixture-context.v1'
 AND context->>'scenario' IN ('chat','embedding','retry','continuation','cancellation')
 AND jsonb_typeof(context->'ordinal') = 'number' AND context->>'ordinal' ~ '^[0-9]+$'
 AND (context->>'ordinal')::numeric BETWEEN 0 AND 1000000) IS TRUE"""


def render_sql(
    schema="public", *, domain_tables=DOMAIN_TABLES, external_uniques=EXTERNAL_UNIQUES
):
    """Accept only the literal reviewed table order and exact external unique tuples."""
    if type(schema) is not str or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema) is None:
        raise ValueError("Exact schema required")
    if (
        tuple(domain_tables) != DOMAIN_TABLES
        or tuple(external_uniques) != EXTERNAL_UNIQUES
    ):
        raise ValueError("Unreviewed downgrade inventory")
    q = '"' + schema + '"'
    table = lambda name: q + '."' + name + '"'
    locks = ", ".join(
        table(name) for name in sorted(set(DOMAIN_TABLES + SHARED_TABLES))
    )
    sql = f"""-- No physical orphan or source journal cleanup is performed.
DO $$ BEGIN
 IF current_setting('transaction_isolation') <> 'read committed' THEN
  RAISE EXCEPTION 'RD downgrade requires READ COMMITTED'; END IF;
 IF NOT pg_try_advisory_xact_lock(1413960260,1129270340) THEN
  RAISE EXCEPTION 'RD downgrade requires quiescent maintenance'; END IF;
END $$;
LOCK TABLE {locks} IN ACCESS EXCLUSIVE MODE NOWAIT;
SET CONSTRAINTS ALL IMMEDIATE;
DO $$ BEGIN
"""
    for name in DOMAIN_TABLES:
        sql += f" IF EXISTS (SELECT 1 FROM {table(name)}) THEN RAISE EXCEPTION 'RD downgrade refuses {name} evidence'; END IF;\n"
    checks = (
        (
            "research_runs",
            "purpose IS DISTINCT FROM 'legacy_strategy' OR engine IS DISTINCT FROM 'legacy'",
        ),
        (
            "research_model_operations",
            "protocol IS DISTINCT FROM 'mineagent.neutral-model.v1'",
        ),
        (
            "research_operation_contexts",
            "context->>'schema_version' IS DISTINCT FROM 'mineagent.neutral-fixture-context.v1'",
        ),
        (
            "research_budget_reservations",
            "purpose NOT IN ('model_transport','process_unit','object_storage')",
        ),
        (
            "research_process_admissions",
            "role='rd_orchestrator' OR profile='mineagent.rdagent-orchestrator.v1'",
        ),
        (
            "research_objects",
            "object_kind NOT IN ('model_request','model_response','process_receipt')",
        ),
        (
            "research_object_publications",
            "object_kind NOT IN ('model_request','model_response','process_receipt')",
        ),
        ("factor_versions", "source_kind='rdagent'"),
        (
            "engine_object_reservation_consumers",
            "consumer_kind IS DISTINCT FROM 'research_object'",
        ),
    )
    for name, predicate in checks:
        sql += f" IF EXISTS (SELECT 1 FROM {table(name)} WHERE {predicate}) THEN RAISE EXCEPTION 'RD downgrade refuses extended {name} evidence'; END IF;\n"
    sql += "END $$;\n"
    # These names are introduced exclusively by018. Table drops remove their
    # remaining domain-local triggers automatically; no shared old trigger touched.
    for name in SHARED_TABLES:
        for trigger in (
            "rd_source_semantics",
            "rd_shared_semantics",
            "a_rd_source_run_lock",
        ):
            sql += f'DROP TRIGGER IF EXISTS "{trigger}" ON {table(name)};\n'
    sql += f"DROP TRIGGER engine_object_claim ON {table('research_object_publications')};\n"
    sql += f"DROP TRIGGER engine_object_consistent ON {table('research_object_publications')};\n"
    for name in reversed(DOMAIN_TABLES):
        sql += f"DROP TABLE {table(name)} RESTRICT;\n"
    sql += f"DROP TABLE {table('engine_object_reservation_consumers')} RESTRICT;\n"
    for name, constraint, _columns in EXTERNAL_UNIQUES:
        sql += f'ALTER TABLE {table(name)} DROP CONSTRAINT "{constraint}" RESTRICT;\n'
    sql += f"""ALTER TABLE {table("research_runs")}
 DROP CONSTRAINT ck_research_run_identity,
 DROP CONSTRAINT ck_research_run_legacy_config,
 ALTER COLUMN strategy_version_id SET NOT NULL,
 ADD CONSTRAINT ck_research_run_identity CHECK (purpose='legacy_strategy' AND engine='legacy'),
 ADD CONSTRAINT ck_research_run_legacy_config CHECK ({q}.mineagent_legacy_run_config(config) IS TRUE);
ALTER TABLE {table("research_process_admissions")}
 DROP CONSTRAINT ck_process_v2_unregistered,
 ADD CONSTRAINT ck_process_v2_unregistered CHECK ({PROFILE_GATE});
ALTER TABLE {table("research_model_operations")}
 DROP CONSTRAINT ck_model_versioned_request,
 DROP COLUMN protocol RESTRICT,
 ADD CHECK ({q}.mineagent_broker_request(request));
ALTER TABLE {table("research_operation_contexts")}
 DROP CONSTRAINT ck_versioned_operation_context,
 ADD CHECK ({OLD_CONTEXT});
ALTER TABLE {table("research_budget_reservations")}
 DROP CONSTRAINT ck_versioned_reservation_purpose,
 ADD CHECK (purpose IN ('model_transport','process_unit','object_storage'));
"""
    for name in ("research_objects", "research_object_publications"):
        sql += f"ALTER TABLE {table(name)} DROP CONSTRAINT ck_{name}_versioned_kind, ADD CHECK (object_kind IN ('model_request','model_response','process_receipt'));\n"
    for signature in FUNCTIONS:
        sql += f"DROP FUNCTION {q}.{signature} RESTRICT;\n"
    return sql
