"""Reduce RD canonical JSON overhead without changing predicates or bounds.

Frozen additive SQL. Installation requires quiescent maintenance.
"""

import re

from alembic import op

revision = "032d_rd_validation_cost"
down_revision = "032c_oe_provider_table_checks"
branch_labels = None
depends_on = None

# fmt: off

UPGRADE_SQL = 'CREATE OR REPLACE FUNCTION @SCHEMA@.mineagent_rd_canonical_json(\n v jsonb, ascii_only boolean, depth integer DEFAULT 0)\nRETURNS text LANGUAGE plpgsql IMMUTABLE STRICT\nSET search_path=pg_catalog AS $$\nDECLARE result text; item record; t text; child text; key_text text;\n        first_item boolean := true; slash text := chr(92);\nBEGIN\n IF current_setting(\'server_encoding\') <> \'UTF8\' THEN\n   RAISE EXCEPTION \'RD canonical metadata requires UTF8\' USING ERRCODE=\'22023\';\n END IF;\n IF depth<0 OR depth>24 THEN\n   RAISE EXCEPTION \'RD canonical metadata depth exceeded\' USING ERRCODE=\'22023\';\n END IF;\n CASE jsonb_typeof(v)\n WHEN \'object\' THEN\n   result:=\'{\';\n   FOR item IN SELECT key,value FROM jsonb_each(v) ORDER BY key COLLATE "C" LOOP\n     IF NOT first_item THEN result:=result||\',\'; END IF;\n     first_item:=false;\n     IF depth>=24 THEN\n       RAISE EXCEPTION \'RD canonical metadata depth exceeded\' USING ERRCODE=\'22023\';\n     END IF;\n     t:=item.key;\n     IF octet_length(t)>1048576 THEN\n       RAISE EXCEPTION \'RD canonical metadata byte bound exceeded\' USING ERRCODE=\'22023\';\n     END IF;\n     IF octet_length(t)=char_length(t) AND position(chr(127) in t)=0 THEN\n       key_text:=pg_catalog.to_json(t)::text;\n     ELSE key_text:=@SCHEMA@.mineagent_rd_canonical_json(to_jsonb(item.key),ascii_only,depth+1);\n     END IF;\n     IF octet_length(key_text)>1048576 THEN\n       RAISE EXCEPTION \'RD canonical metadata byte bound exceeded\' USING ERRCODE=\'22023\';\n     END IF;\n     CASE jsonb_typeof(item.value)\n     WHEN \'string\' THEN\n       t:=item.value#>>\'{}\';\n       IF octet_length(t)>1048576 THEN\n         RAISE EXCEPTION \'RD canonical metadata byte bound exceeded\' USING ERRCODE=\'22023\';\n       END IF;\n       IF octet_length(t)=char_length(t) AND position(chr(127) in t)=0 THEN\n         child:=pg_catalog.to_json(t)::text;\n       ELSE child:=@SCHEMA@.mineagent_rd_canonical_json(item.value,ascii_only,depth+1);\n       END IF;\n     WHEN \'number\' THEN\n       child:=item.value::text;\n       IF child !~ \'^(0|-?[1-9][0-9]*)$\' THEN\n         RAISE EXCEPTION \'RD canonical metadata requires integer tokens\' USING ERRCODE=\'22023\';\n       END IF;\n     WHEN \'boolean\', \'null\' THEN child:=item.value::text;\n     ELSE child:=@SCHEMA@.mineagent_rd_canonical_json(item.value,ascii_only,depth+1);\n     END CASE;\n     IF octet_length(child)>1048576 THEN\n       RAISE EXCEPTION \'RD canonical metadata byte bound exceeded\' USING ERRCODE=\'22023\';\n     END IF;\n     result:=result||key_text||\':\'||child;\n     IF octet_length(result)>1048576 THEN\n       RAISE EXCEPTION \'RD canonical metadata byte bound exceeded\' USING ERRCODE=\'22023\';\n     END IF;\n   END LOOP;\n   result:=result||\'}\';\n WHEN \'array\' THEN\n   result:=\'[\';\n   FOR item IN SELECT value FROM jsonb_array_elements(v) WITH ORDINALITY a(value,ord) ORDER BY ord LOOP\n     IF NOT first_item THEN result:=result||\',\'; END IF;\n     first_item:=false;\n     IF depth>=24 THEN\n       RAISE EXCEPTION \'RD canonical metadata depth exceeded\' USING ERRCODE=\'22023\';\n     END IF;\n     CASE jsonb_typeof(item.value)\n     WHEN \'string\' THEN\n       t:=item.value#>>\'{}\';\n       IF octet_length(t)>1048576 THEN\n         RAISE EXCEPTION \'RD canonical metadata byte bound exceeded\' USING ERRCODE=\'22023\';\n       END IF;\n       IF octet_length(t)=char_length(t) AND position(chr(127) in t)=0 THEN\n         child:=pg_catalog.to_json(t)::text;\n       ELSE child:=@SCHEMA@.mineagent_rd_canonical_json(item.value,ascii_only,depth+1);\n       END IF;\n     WHEN \'number\' THEN\n       child:=item.value::text;\n       IF child !~ \'^(0|-?[1-9][0-9]*)$\' THEN\n         RAISE EXCEPTION \'RD canonical metadata requires integer tokens\' USING ERRCODE=\'22023\';\n       END IF;\n     WHEN \'boolean\', \'null\' THEN child:=item.value::text;\n     ELSE child:=@SCHEMA@.mineagent_rd_canonical_json(item.value,ascii_only,depth+1);\n     END CASE;\n     IF octet_length(child)>1048576 THEN\n       RAISE EXCEPTION \'RD canonical metadata byte bound exceeded\' USING ERRCODE=\'22023\';\n     END IF;\n     result:=result||child;\n     IF octet_length(result)>1048576 THEN\n       RAISE EXCEPTION \'RD canonical metadata byte bound exceeded\' USING ERRCODE=\'22023\';\n     END IF;\n   END LOOP;\n   result:=result||\']\';\n WHEN \'string\' THEN\n   result:=chr(34); t:=v#>>\'{}\';\n   IF octet_length(t)>1048576 THEN\n     RAISE EXCEPTION \'RD canonical metadata byte bound exceeded\' USING ERRCODE=\'22023\';\n   END IF;\n   IF t COLLATE "C" !~ \'[^ -~]\' AND position(chr(34) in t)=0 AND position(chr(92) in t)=0 THEN\n     result:=chr(34)||t||chr(34);\n   ELSIF octet_length(t)=char_length(t) AND position(chr(127) in t)=0 THEN\n     result:=pg_catalog.to_json(t)::text;\n   ELSE\n   SELECT chr(34)||coalesce(string_agg(\n     CASE z.p\n       WHEN 8 THEN slash||\'b\' WHEN 9 THEN slash||\'t\'\n       WHEN 10 THEN slash||\'n\' WHEN 12 THEN slash||\'f\'\n       WHEN 13 THEN slash||\'r\' WHEN 34 THEN slash||chr(34)\n       WHEN 92 THEN slash||slash\n       ELSE CASE\n         WHEN z.p<32 OR (ascii_only AND z.p>=127 AND z.p<=65535)\n           THEN slash||\'u\'||lpad(to_hex(z.p),4,\'0\')\n         WHEN ascii_only AND z.p>65535\n           THEN slash||\'u\'||lpad(to_hex(55296+(z.p-65536)/1024),4,\'0\')\n             ||slash||\'u\'||lpad(to_hex(56320+(z.p-65536)%1024),4,\'0\')\n         ELSE z.c END\n       END, \'\' ORDER BY z.ord), \'\')||chr(34)\n   INTO result\n   FROM (SELECT chars.c, chars.ord, ascii(chars.c) AS p\n         FROM regexp_split_to_table(t,\'\') WITH ORDINALITY AS chars(c,ord) WHERE chars.c<>\'\') z;\n   END IF;\n WHEN \'number\' THEN\n   result:=v::text;\n   IF result !~ \'^(0|-?[1-9][0-9]*)$\' THEN\n     RAISE EXCEPTION \'RD canonical metadata requires integer tokens\' USING ERRCODE=\'22023\';\n   END IF;\n WHEN \'boolean\' THEN result:=v::text;\n WHEN \'null\' THEN result:=\'null\';\n ELSE\n   RAISE EXCEPTION \'Unsupported RD canonical metadata type\' USING ERRCODE=\'22023\';\n END CASE;\n IF octet_length(result)>1048576 THEN\n   RAISE EXCEPTION \'RD canonical metadata byte bound exceeded\' USING ERRCODE=\'22023\';\n END IF;\n RETURN result;\nEND $$;'

DOWNGRADE_SQL = 'CREATE OR REPLACE FUNCTION @SCHEMA@.mineagent_rd_canonical_json(\n v jsonb, ascii_only boolean, depth integer DEFAULT 0)\nRETURNS text LANGUAGE plpgsql IMMUTABLE STRICT\nSET search_path=pg_catalog AS $$\nDECLARE result text; item record; t text;\n        first_item boolean := true; slash text := chr(92);\nBEGIN\n IF current_setting(\'server_encoding\') <> \'UTF8\' THEN\n   RAISE EXCEPTION \'RD canonical metadata requires UTF8\' USING ERRCODE=\'22023\';\n END IF;\n IF depth<0 OR depth>24 THEN\n   RAISE EXCEPTION \'RD canonical metadata depth exceeded\' USING ERRCODE=\'22023\';\n END IF;\n CASE jsonb_typeof(v)\n WHEN \'object\' THEN\n   result:=\'{\';\n   FOR item IN SELECT key,value FROM jsonb_each(v) ORDER BY key COLLATE "C" LOOP\n     IF NOT first_item THEN result:=result||\',\'; END IF;\n     first_item:=false;\n     result:=result||@SCHEMA@.mineagent_rd_canonical_json(to_jsonb(item.key),ascii_only,depth+1)\n       ||\':\'||@SCHEMA@.mineagent_rd_canonical_json(item.value,ascii_only,depth+1);\n     IF octet_length(result)>1048576 THEN\n       RAISE EXCEPTION \'RD canonical metadata byte bound exceeded\' USING ERRCODE=\'22023\';\n     END IF;\n   END LOOP;\n   result:=result||\'}\';\n WHEN \'array\' THEN\n   result:=\'[\';\n   FOR item IN SELECT value FROM jsonb_array_elements(v) WITH ORDINALITY a(value,ord) ORDER BY ord LOOP\n     IF NOT first_item THEN result:=result||\',\'; END IF;\n     first_item:=false;\n     result:=result||@SCHEMA@.mineagent_rd_canonical_json(item.value,ascii_only,depth+1);\n     IF octet_length(result)>1048576 THEN\n       RAISE EXCEPTION \'RD canonical metadata byte bound exceeded\' USING ERRCODE=\'22023\';\n     END IF;\n   END LOOP;\n   result:=result||\']\';\n WHEN \'string\' THEN\n   result:=chr(34); t:=v#>>\'{}\';\n   IF octet_length(t)>1048576 THEN\n     RAISE EXCEPTION \'RD canonical metadata byte bound exceeded\' USING ERRCODE=\'22023\';\n   END IF;\n   IF t COLLATE "C" !~ \'[^ -~]\' AND position(chr(34) in t)=0 AND position(chr(92) in t)=0 THEN\n     result:=chr(34)||t||chr(34);\n   ELSIF octet_length(t)=char_length(t) AND position(chr(127) in t)=0 THEN\n     result:=pg_catalog.to_json(t)::text;\n   ELSE\n   SELECT chr(34)||coalesce(string_agg(\n     CASE z.p\n       WHEN 8 THEN slash||\'b\' WHEN 9 THEN slash||\'t\'\n       WHEN 10 THEN slash||\'n\' WHEN 12 THEN slash||\'f\'\n       WHEN 13 THEN slash||\'r\' WHEN 34 THEN slash||chr(34)\n       WHEN 92 THEN slash||slash\n       ELSE CASE\n         WHEN z.p<32 OR (ascii_only AND z.p>=127 AND z.p<=65535)\n           THEN slash||\'u\'||lpad(to_hex(z.p),4,\'0\')\n         WHEN ascii_only AND z.p>65535\n           THEN slash||\'u\'||lpad(to_hex(55296+(z.p-65536)/1024),4,\'0\')\n             ||slash||\'u\'||lpad(to_hex(56320+(z.p-65536)%1024),4,\'0\')\n         ELSE z.c END\n       END, \'\' ORDER BY z.ord), \'\')||chr(34)\n   INTO result\n   FROM (SELECT chars.c, chars.ord, ascii(chars.c) AS p\n         FROM regexp_split_to_table(t,\'\') WITH ORDINALITY AS chars(c,ord) WHERE chars.c<>\'\') z;\n   END IF;\n WHEN \'number\' THEN\n   result:=v::text;\n   IF result !~ \'^(0|-?[1-9][0-9]*)$\' THEN\n     RAISE EXCEPTION \'RD canonical metadata requires integer tokens\' USING ERRCODE=\'22023\';\n   END IF;\n WHEN \'boolean\' THEN result:=v::text;\n WHEN \'null\' THEN result:=\'null\';\n ELSE\n   RAISE EXCEPTION \'Unsupported RD canonical metadata type\' USING ERRCODE=\'22023\';\n END CASE;\n IF octet_length(result)>1048576 THEN\n   RAISE EXCEPTION \'RD canonical metadata byte bound exceeded\' USING ERRCODE=\'22023\';\n END IF;\n RETURN result;\nEND $$;'

# fmt: on


def _schema():
    value = op.get_bind().exec_driver_sql("SELECT current_schema()").scalar_one()
    if type(value) is not str or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value) is None:
        raise RuntimeError("Exact installation schema required")
    return '"' + value + '"'


def _maintenance(schema):
    op.execute("""DO $$ BEGIN
    IF current_setting('transaction_isolation') <> 'read committed'
    OR NOT pg_try_advisory_xact_lock(1413960260,1129270340)
    THEN RAISE EXCEPTION 'RD validation cost migration requires quiescent maintenance'; END IF;
    END $$;""")
    tables = (
        "artifacts",
        "candidate_bookmarks",
        "comparison_submissions",
        "comparisons",
        "controlled_arms",
        "controlled_experiments",
        "data_blocks",
        "data_imports",
        "data_outbox",
        "data_usage_exposures",
        "dataset_versions",
        "declared_upload_basket_members",
        "declared_upload_baskets",
        "engine_object_reservation_consumers",
        "execution_attempts",
        "experiments",
        "factor_analysis_plans",
        "factor_bundle_members",
        "factor_bundles",
        "factor_candidate_input_files",
        "factor_candidate_input_inventories",
        "factor_comparisons",
        "factor_configured_input_witnesses",
        "factor_controller_launches",
        "factor_cutoff_executions",
        "factor_cutoff_outcomes",
        "factor_direct_analysis_intents",
        "factor_direct_analysis_outcomes",
        "factor_evaluation_outcomes",
        "factor_evaluation_result_consumers",
        "factor_evaluation_starts",
        "factor_evaluations",
        "factor_execution_evaluation_outcomes",
        "factor_execution_snapshot_objects",
        "factor_execution_snapshot_outcomes",
        "factor_execution_snapshot_partitions",
        "factor_execution_snapshot_publications",
        "factor_generation_boundaries",
        "factor_generation_model_operations",
        "factor_generation_observations",
        "factor_generation_outcomes",
        "factor_generation_sources",
        "factor_generation_starts",
        "factor_import_admissions",
        "factor_import_envelopes",
        "factor_native_normalizer_inputs",
        "factor_native_objects",
        "factor_native_outcomes",
        "factor_native_publications",
        "factor_numeric_failure_feedbacks",
        "factor_objects",
        "factor_packages",
        "factor_research_attempt_bindings",
        "factor_research_checkpoints",
        "factor_research_specs",
        "factor_research_submissions",
        "factor_response_assemblies",
        "factor_response_assembly_parts",
        "factor_seeded_submissions",
        "factor_source_publication_outcomes",
        "factor_source_publications",
        "factor_source_read_parts",
        "factor_source_read_witnesses",
        "factor_strategy_decisions",
        "factor_strategy_execution_outcomes",
        "factor_strategy_executions",
        "factor_strategy_fills",
        "factor_strategy_orders",
        "factor_submissions",
        "factor_upstream_call_outcomes",
        "factor_upstream_calls",
        "factor_versions",
        "factor_workflow_submissions",
        "factors",
        "feature_snapshot_objects",
        "feature_snapshots",
        "final_selections",
        "independent_evaluations",
        "instruments",
        "listings",
        "oe_structured_candidates",
        "oe_structured_checkpoint_chunks",
        "oe_structured_checkpoint_completions",
        "oe_structured_checkpoint_intents",
        "oe_structured_checkpoint_publications",
        "oe_structured_checkpoint_uploads",
        "oe_structured_control_states",
        "oe_structured_engine_outcomes",
        "oe_structured_epochs",
        "oe_structured_executions",
        "oe_structured_iteration_intents",
        "oe_structured_provider_exchanges",
        "oe_structured_provider_receipts",
        "oe_structured_provider_transports",
        "oe_structured_sampling_intents",
        "oe_structured_specs",
        "oe_structured_submissions",
        "oe_structured_terminal_applications",
        "oe_structured_worker_outcomes",
        "oe_structured_worker_placements",
        "oe_workflow_submissions",
        "outbox_events",
        "owner_listings",
        "provider_budgets",
        "provider_connections",
        "rd_provider_transport_receipts",
        "rd_steer_versions",
        "rd_steers",
        "research_budget_controls",
        "research_budget_reservations",
        "research_budget_settlements",
        "research_candidates",
        "research_checkpoints",
        "research_context_aborts",
        "research_evaluations",
        "research_input_derivation_events",
        "research_input_derivations",
        "research_input_objects",
        "research_input_records",
        "research_input_versions",
        "research_model_calls",
        "research_model_operations",
        "research_model_outcomes",
        "research_model_transports",
        "research_note_evidence_pins",
        "research_note_normal_oe_pins",
        "research_note_revisions",
        "research_notes",
        "research_object_outcomes",
        "research_object_publications",
        "research_objects",
        "research_operation_contexts",
        "research_process_admissions",
        "research_process_receipts",
        "research_process_states",
        "research_runs",
        "research_workflow_checkpoints",
        "research_workflow_commands",
        "research_workflow_model_consumption",
        "research_workflow_revisions",
        "research_workflow_stages",
        "research_workflows",
        "run_events",
        "search_candidate_grants",
        "search_candidates",
        "search_checkpoint_chunks",
        "search_checkpoint_files",
        "search_checkpoint_intents",
        "search_checkpoint_publications",
        "search_checkpoint_upload_completions",
        "search_checkpoint_uploads",
        "search_control_transitions",
        "search_controller_event_receipts",
        "search_controller_launches",
        "search_epoch_aborts",
        "search_evaluation_inputs",
        "search_evaluation_outcomes",
        "search_evaluation_preparations",
        "search_evaluations",
        "search_iteration_intents",
        "search_objective_acknowledgements",
        "search_objective_evaluations",
        "search_objective_receipts",
        "search_proposal_responses",
        "search_proposal_sources",
        "search_rendered_contexts",
        "search_resolved_execution_profiles",
        "search_terminal_applications",
        "search_worker_assignment_closures",
        "search_worker_assignments",
        "search_worker_expected_identities",
        "search_worker_outcomes",
        "search_worker_tickets",
        "split_versions",
        "strategy_factor_dependencies",
        "strategy_packages",
        "strategy_search_epochs",
        "strategy_search_specs",
        "strategy_spec_revisions",
        "strategy_versions",
        "structured_strategy_specs",
        "structured_strategy_versions",
        "universe_versions",
        "user_workspace_settings",
        "users",
        "vendor_mappings",
        "worker_heartbeats",
    )
    op.execute(
        "LOCK TABLE "
        + ",".join(schema + "." + name for name in tables)
        + " IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    op.execute("SET LOCAL search_path = " + schema + ",pg_catalog")


def upgrade():
    schema = _schema()
    _maintenance(schema)
    op.get_bind().exec_driver_sql(
        UPGRADE_SQL.replace("@SCHEMA@", schema),
        execution_options={"no_parameters": True},
    )


def downgrade():
    schema = _schema()
    _maintenance(schema)
    op.get_bind().exec_driver_sql(
        DOWNGRADE_SQL.replace("@SCHEMA@", schema),
        execution_options={"no_parameters": True},
    )
