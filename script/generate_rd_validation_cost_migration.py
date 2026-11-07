"""Freeze the additive RD validation cost revision; never rewrite predecessors."""

import hashlib
from pathlib import Path

from backend.app.rdagent_validation_performance_sql import (
    render_downgrade_sql,
    render_upgrade_sql,
)

ROOT = Path(__file__).resolve().parents[1]
# Frozen installation tables through032c. Functions are shared transitively across
# RD, structured factor, and OE graphs; maintenance excludes all application writes.
TABLES = (
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


def document():
    previous = ROOT / "backend/alembic/versions/032c_oe_provider_table_checks.py"
    source = previous.read_bytes()
    if (
        hashlib.sha256(source).hexdigest()
        != "9c97c2a8248887fbd3e4024c99e7da58c077a03ebea78ecbd8d10f32749f9cca"
    ):
        raise ValueError("Frozen032c migration source differs")
    tail = source.decode().split("# fmt: on", 1)[1]
    start = tail.index("    tables = (")
    end = tail.index("    op.execute(", start)
    tail = (
        tail[:start]
        + "    tables = (\n"
        + "".join('        "' + table + '",\n' for table in TABLES)
        + "    )\n"
        + tail[end:]
    )
    tail = tail.replace("Contract repair migration", "RD validation cost migration")
    header = '''"""Reduce RD canonical JSON overhead without changing predicates or bounds.

Frozen additive SQL. Installation requires quiescent maintenance.
"""

import re

from alembic import op

revision = "032d_rd_validation_cost"
down_revision = "032c_oe_provider_table_checks"
branch_labels = None
depends_on = None

# fmt: off
'''
    for name, value in (
        ("UPGRADE_SQL", render_upgrade_sql()),
        ("DOWNGRADE_SQL", render_downgrade_sql()),
    ):
        header += "\n" + name + " = " + repr(value) + "\n"
    return header + "\n# fmt: on" + tail


if __name__ == "__main__":
    (ROOT / "backend/alembic/versions/032d_rd_validation_cost.py").write_text(
        document()
    )
