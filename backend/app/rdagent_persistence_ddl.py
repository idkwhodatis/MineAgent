"""Revision018 composition under development; not an execution/registration API.

The predecessor is017_numeric_process_boundary. Historical validators are retained;
new predicates and protocol/engine branches are explicit, not provider wildcards.
"""

from backend.app.numeric_process_ddl import PROFILE_GATE as NUMERIC_PROFILE_GATE

NEUTRAL_PROTOCOL = "mineagent.neutral-model.v1"
CONTROLLER_PROFILE_GATE = f"""(({NUMERIC_PROFILE_GATE}) OR
 (schema_version='mineagent.process-admission.v2' AND role='rd_orchestrator'
  AND profile='mineagent.rdagent-orchestrator.v1' AND ipc_policy='host_ticket'
  AND ticket_sha256 IS NOT NULL AND ticket_sha256 ~ '^[a-f0-9]{{64}}$')) IS TRUE"""

RUN_IDENTITY = "((purpose='legacy_strategy' AND engine='legacy') OR (purpose='factor_research' AND engine='rdagent'))"
RUN_CONFIG = """((purpose='legacy_strategy' AND engine='legacy' AND strategy_version_id IS NOT NULL
 AND mineagent_legacy_run_config(config) IS TRUE) OR
 (purpose='factor_research' AND engine='rdagent' AND strategy_version_id IS NULL
 AND experiment_id IS NULL AND split_version_id IS NULL AND final_selection_id IS NULL
 AND submission_binding IS NULL AND portable_policy_image_digest IS NULL
 AND latest_checkpoint_id IS NULL AND resume_checkpoint_id IS NULL
 AND mineagent_rd_source_config(config) IS TRUE
 AND dataset_version_id::text=config->'input'->'dataset'->>'dataset_version_id'
 AND request_sha256=encode(sha256(convert_to(mineagent_rd_canonical_json(config,true),'UTF8')),'hex')
 AND (research_image_digest IS NULL OR research_image_digest=config->'runtime'->>'orchestrator_image'))) IS TRUE"""


def _replace_original_check(table, marker, name, expression):
    """Exactly one known historical predicate must be present; no broad DROP loop."""
    return f"""DO $$ DECLARE names text[]; BEGIN
 SELECT array_agg(conname) INTO names FROM pg_constraint
 WHERE conrelid='{table}'::regclass AND contype='c'
 AND position('{marker}' IN pg_get_constraintdef(oid))>0;
 IF cardinality(names) IS DISTINCT FROM 1 THEN RAISE EXCEPTION 'Expected exact historical {table} predicate'; END IF;
 EXECUTE format('ALTER TABLE {table} DROP CONSTRAINT %I',names[1]);
 END $$;
 ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({expression});"""


def shared_upgrade_sql():
    sql = f"""ALTER TABLE research_runs ALTER COLUMN strategy_version_id DROP NOT NULL,
 DROP CONSTRAINT ck_research_run_identity, DROP CONSTRAINT ck_research_run_legacy_config,
 ADD CONSTRAINT ck_research_run_identity CHECK ({RUN_IDENTITY}),
 ADD CONSTRAINT ck_research_run_legacy_config CHECK ({RUN_CONFIG});
 ALTER TABLE research_model_operations ADD COLUMN protocol varchar(60) NOT NULL DEFAULT '{NEUTRAL_PROTOCOL}';
 ALTER TABLE research_process_admissions DROP CONSTRAINT ck_process_v2_unregistered,
 ADD CONSTRAINT ck_process_v2_unregistered CHECK ({CONTROLLER_PROFILE_GATE});
 """
    sql += _replace_original_check(
        "research_model_operations",
        "mineagent_broker_request(request)",
        "ck_model_versioned_request",
        "((protocol='mineagent.neutral-model.v1' AND mineagent_broker_request(request) IS TRUE) OR (protocol IN ('mineagent.rdagent-chat.v1','mineagent.rdagent-embedding.v1') AND mineagent_rd_source_request(request,protocol) IS TRUE))",
    )
    sql += _replace_original_check(
        "research_operation_contexts",
        "mineagent.neutral-fixture-context.v1",
        "ck_versioned_operation_context",
        "((jsonb_typeof(context)='object' AND context ?& ARRAY['schema_version','scenario','ordinal'] AND context-ARRAY['schema_version','scenario','ordinal']='{}'::jsonb AND context->>'schema_version'='mineagent.neutral-fixture-context.v1' AND context->>'scenario' IN ('chat','embedding','retry','continuation','cancellation') AND jsonb_typeof(context->'ordinal')='number' AND context->>'ordinal' ~ '^[0-9]+$' AND (context->>'ordinal')::numeric BETWEEN 0 AND 1000000) IS TRUE OR mineagent_rd_source_context(context) IS TRUE)",
    )
    sql += _replace_original_check(
        "research_budget_reservations",
        "model_transport",
        "ck_versioned_reservation_purpose",
        "purpose IN ('model_transport','process_unit','object_storage','engine_mutation')",
    )
    for table in ("research_objects", "research_object_publications"):
        sql += _replace_original_check(
            table,
            "model_request",
            "ck_" + table + "_versioned_kind",
            "object_kind IN ('model_request','model_response','process_receipt','factor_response_assembly')",
        )
    return sql


CLOSED_OUTCOME_TABLES = (
    "factor_generation_outcomes",
    "factor_evaluation_outcomes",
    "factor_cutoff_outcomes",
    "factor_research_checkpoints",
    "factor_generation_observations",
    "factor_generation_boundaries",
    "factor_candidate_input_inventories",
    "factor_candidate_input_files",
)


def closed_phase_sql():
    sql = """CREATE FUNCTION mineagent_rd_source_only_phase() RETURNS trigger
    LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'RD finalization/adoption/resume phase is not admitted by source persistence'; END $$;
    """
    return sql + "\n".join(
        f"CREATE TRIGGER rd_source_phase_closed BEFORE INSERT ON {name} FOR EACH ROW EXECUTE FUNCTION mineagent_rd_source_only_phase();"
        for name in CLOSED_OUTCOME_TABLES
    )
