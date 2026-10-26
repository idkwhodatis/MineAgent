"""Revision018 RD source evidence closure, never execution or resume authority.

The caller must independently validate research/factor closures and all physical
inventory, including conservative orphans. This module rereads bounded retained
source/response/assembly/journal bytes and reexecutes only the pinned stdlib JSON
parser. No HDF, candidate source, pickle or upstream runtime is executed.
"""

import hashlib
import json
from collections import defaultdict
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import text

from backend.app.schema_capabilities import capabilities_for
from packages.factors.contracts import SnapshotInput, canonical_bytes, digest
from packages.rdagent_adapter.host_evidence import (
    ResponseAssemblyDocument,
    research_definition,
    research_snapshot_input,
)
from packages.rdagent_adapter.operational_bootstrap import RDOperationalBootstrap
from packages.rdagent_adapter.prefix import PrefixManifest
from packages.rdagent_adapter.response_parser import (
    UPSTREAM_FRAGMENT_SHA256,
    parse_selected_responses,
)
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.engine_broker_contracts import (
    RDChatResponse,
    RDEmbeddingResponse,
    parse_request,
    request_sha256,
    validate_response,
)
from packages.research_runtime.engine_broker_contracts import (
    canonical_bytes as broker_bytes,
)

SCHEMA = "mineagent.rd-source-restore-closure.v1"
SCOPE = ("owner_id", "run_id", "attempt_id", "generation")
MAX_BYTES = 32_000_000
# Literal inventory: changing a model never silently widens a backup grammar.
FIELDS = {
    "factor_research_specs": "config config_sha256 input_sha256 id owner_id run_id created_at",
    "factor_generation_starts": "spec_id logical_step task_index context_id mutation_reservation_id parent_factor_version_id attempt_id generation id owner_id run_id created_at",
    "factor_upstream_calls": "start_id context_id phase kind ordinal attempt_id generation id owner_id run_id created_at",
    "factor_response_assemblies": "start_id context_id call_id object_id object_kind object_sha256 part_count attempt_id generation id owner_id run_id created_at",
    "factor_generation_sources": "context_id start_id factor_version_id call_id assembly_id source_sha256 attempt_id generation id owner_id run_id created_at",
    "factor_generation_outcomes": "start_id logical_step outcome factor_version_id proposal_object_id feedback_object_id error_code evaluated_source_id adopted_source_id adopted_factor_version_id continuation_source_id continuation_factor_version_id adoption_object_id adoption_object_kind adoption_sha256 attempt_id generation id owner_id run_id created_at",
    "factor_generation_model_operations": "context_id start_id call_id idempotency_key operation_id request_ordinal relation predecessor_id protocol parse_failure_object_id parse_failure_object_kind parse_failure_sha256 attempt_id generation id owner_id run_id created_at",
    "factor_response_assembly_parts": "call_id assembly_id member_id outcome_id response_object_id response_object_kind response_sha256 ordinal attempt_id generation id owner_id run_id created_at",
    "factor_evaluation_starts": "start_id evaluated_source_id factor_version_id snapshot_input input_sha256 expected_cutoffs attempt_id generation id owner_id run_id created_at",
    "factor_evaluation_outcomes": "evaluation_id status snapshot_id snapshot_input_sha256 snapshot_output_sha256 feedback_object_id feedback_object_kind feedback_sha256 attempt_id generation id owner_id run_id created_at",
    "factor_cutoff_executions": "evaluation_id start_id factor_version_id cutoff_ordinal projection projection_sha256 candidate_admission_id evaluated_source_id attempt_id generation id owner_id run_id created_at",
    "factor_cutoff_outcomes": "execution_id status normalizer_admission_id hdf_object_id values_object_id row_count error_code hdf_object_kind hdf_sha256 values_object_kind values_sha256 attempt_id generation id owner_id run_id created_at",
    "factor_research_checkpoints": "spec_id completed_step last_outcome_id state state_sha256 attempt_id generation id owner_id run_id created_at",
    "factor_research_attempt_bindings": "spec_id resumed_checkpoint_id attempt_id generation id owner_id run_id created_at",
    "factor_source_publications": "start_id call_id assembly_object_id assembly_object_kind assembly_sha256 budget_reservation_id source_sha256 size_bytes peak_bytes factor_id factor_version_id attempt_id generation id owner_id run_id created_at",
    "factor_source_publication_outcomes": "publication_id source_id journal_sha256 attempt_id generation id owner_id run_id created_at",
    "factor_controller_launches": "attempt_binding_id admission_id bootstrap bootstrap_sha256 attempt_id generation id owner_id run_id created_at",
    "factor_upstream_call_outcomes": "call_id controller_launch_id status member_count member_ids response_outcome_ids attempt_id generation id owner_id run_id created_at",
    "factor_source_read_witnesses": "source_id assembly_id call_id controller_launch_id parser_profile parser_source_sha256 source_sha256 source_size_bytes source_object_id source_object_kind raw_text_sha256 raw_text_bytes normalized_json_sha256 normalized_json_bytes selected_first_ordinal part_count witness_sha256 attempt_id generation id owner_id run_id created_at",
    "factor_source_read_parts": "witness_id assembly_part_id member_id outcome_id response_object_id response_object_kind response_sha256 ordinal finish_reason content_sha256 content_size_bytes attempt_id generation id owner_id run_id created_at",
    "factor_generation_observations": "start_id controller_launch_id logical_step decision decision_sha256 qualification attempt_id generation id owner_id run_id created_at",
    "factor_generation_boundaries": "start_id observation_id controller_launch_id logical_step boundary next_logical_step attempt_id generation id owner_id run_id created_at",
    "factor_candidate_input_inventories": "execution_id admission_id input_sha256 projection_sha256 file_count attempt_id generation id owner_id run_id created_at",
    "factor_candidate_input_files": "inventory_id destination ordinal object_kind sha256 size_bytes factor_object_id research_object_id publication_id attempt_id generation id owner_id run_id created_at",
}
RUN_FIELDS = "id owner_id idempotency_key request_sha256 submission_binding config purpose engine dataset_version_id strategy_version_id experiment_id split_version_id final_selection_id status generation active_attempt_id research_image_digest portable_policy_image_digest latest_checkpoint_id resume_checkpoint_id next_event_seq result error created_at updated_at"
CONSUMER_FIELDS = (
    "reservation_id owner_id run_id attempt_id generation consumer_kind consumer_id"
)
EXTERNAL_FIELDS = {"runs": RUN_FIELDS, "datasets": "id owner_id content_sha256"}
PHASE_EMPTY = frozenset(
    {
        "factor_generation_outcomes",
        "factor_evaluation_outcomes",
        "factor_cutoff_outcomes",
        "factor_research_checkpoints",
        "factor_generation_observations",
        "factor_generation_boundaries",
    }
)

UNIQUE_KEYS = {
    "factor_research_specs": [("run_id",), ("owner_id", "run_id", "id")],
    "factor_generation_starts": [
        ("mutation_reservation_id",),
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ("owner_id", "run_id", "attempt_id", "generation", "id", "logical_step"),
        ("owner_id", "run_id", "attempt_id", "generation", "id", "context_id"),
        ("run_id", "generation", "logical_step", "task_index"),
    ],
    "factor_upstream_calls": [
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ("start_id", "phase", "ordinal"),
        (
            "owner_id",
            "run_id",
            "attempt_id",
            "generation",
            "start_id",
            "context_id",
            "id",
        ),
    ],
    "factor_response_assemblies": [
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ("owner_id", "run_id", "attempt_id", "generation", "call_id", "id"),
        (
            "owner_id",
            "run_id",
            "attempt_id",
            "generation",
            "start_id",
            "context_id",
            "call_id",
            "id",
        ),
        ("call_id",),
    ],
    "factor_generation_sources": [
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ("factor_version_id",),
        ("owner_id", "run_id", "id", "factor_version_id"),
        ("start_id",),
        (
            "owner_id",
            "run_id",
            "attempt_id",
            "generation",
            "start_id",
            "factor_version_id",
        ),
    ],
    "factor_generation_outcomes": [
        ("owner_id", "run_id", "attempt_id", "generation", "id", "logical_step"),
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ("start_id",),
    ],
    "factor_generation_model_operations": [
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ("operation_id",),
        ("call_id", "idempotency_key"),
        ("call_id", "request_ordinal"),
        ("owner_id", "run_id", "attempt_id", "generation", "call_id", "id"),
    ],
    "factor_response_assembly_parts": [
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ("assembly_id", "ordinal"),
        ("assembly_id", "member_id"),
    ],
    "factor_evaluation_starts": [
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ("start_id",),
        (
            "owner_id",
            "run_id",
            "attempt_id",
            "generation",
            "id",
            "start_id",
            "evaluated_source_id",
            "factor_version_id",
        ),
    ],
    "factor_evaluation_outcomes": [
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ("evaluation_id",),
    ],
    "factor_cutoff_executions": [
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ("candidate_admission_id",),
        (
            "owner_id",
            "run_id",
            "attempt_id",
            "generation",
            "id",
            "candidate_admission_id",
            "evaluated_source_id",
        ),
        ("start_id", "cutoff_ordinal"),
    ],
    "factor_cutoff_outcomes": [
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ("execution_id",),
    ],
    "factor_research_checkpoints": [
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ("owner_id", "run_id", "id"),
        ("run_id", "completed_step"),
    ],
    "factor_research_attempt_bindings": [
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ("attempt_id",),
    ],
    "factor_source_publications": [
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ("start_id",),
        ("budget_reservation_id",),
        ("factor_version_id",),
    ],
    "factor_source_publication_outcomes": [
        ("publication_id",),
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ("source_id",),
    ],
    "factor_controller_launches": [
        ("attempt_binding_id",),
        ("admission_id",),
        ("attempt_id",),
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ],
    "factor_upstream_call_outcomes": [
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ("call_id",),
    ],
    "factor_source_read_witnesses": [
        ("assembly_id",),
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ("source_id",),
    ],
    "factor_source_read_parts": [
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ("witness_id", "ordinal"),
        ("witness_id", "member_id"),
    ],
    "factor_generation_observations": [
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ("start_id",),
        (
            "owner_id",
            "run_id",
            "attempt_id",
            "generation",
            "id",
            "start_id",
            "logical_step",
        ),
    ],
    "factor_generation_boundaries": [
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ("start_id",),
        ("observation_id",),
    ],
    "factor_candidate_input_inventories": [
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ("execution_id",),
        ("admission_id",),
    ],
    "factor_candidate_input_files": [
        ("inventory_id", "destination"),
        ("inventory_id", "ordinal"),
        ("owner_id", "run_id", "attempt_id", "generation", "id"),
    ],
}
FOREIGN_KEYS = {
    "factor_research_specs": [
        (("owner_id", "run_id"), "research_runs", ("owner_id", "id"))
    ],
    "factor_generation_starts": [
        (
            ("run_id", "generation", "attempt_id"),
            "execution_attempts",
            ("run_id", "generation", "id"),
        ),
        (("owner_id", "run_id"), "research_runs", ("owner_id", "id")),
        (
            (
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "mutation_reservation_id",
            ),
            "research_budget_reservations",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
        (
            ("owner_id", "parent_factor_version_id"),
            "factor_versions",
            ("owner_id", "id"),
        ),
        (
            ("owner_id", "run_id", "attempt_id", "generation", "context_id"),
            "research_operation_contexts",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
        (
            ("owner_id", "run_id", "spec_id"),
            "factor_research_specs",
            ("owner_id", "run_id", "id"),
        ),
    ],
    "factor_upstream_calls": [
        (
            (
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "context_id",
                "start_id",
            ),
            "factor_generation_starts",
            ("owner_id", "run_id", "attempt_id", "generation", "context_id", "id"),
        ),
        (
            ("run_id", "generation", "attempt_id"),
            "execution_attempts",
            ("run_id", "generation", "id"),
        ),
        (("owner_id", "run_id"), "research_runs", ("owner_id", "id")),
    ],
    "factor_response_assemblies": [
        (
            (
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "start_id",
                "context_id",
                "call_id",
            ),
            "factor_upstream_calls",
            (
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "start_id",
                "context_id",
                "id",
            ),
        ),
        (
            ("owner_id", "object_id", "object_kind", "object_sha256"),
            "research_objects",
            ("owner_id", "id", "object_kind", "sha256"),
        ),
        (
            ("run_id", "generation", "attempt_id"),
            "execution_attempts",
            ("run_id", "generation", "id"),
        ),
        (("owner_id", "run_id"), "research_runs", ("owner_id", "id")),
    ],
    "factor_generation_sources": [
        (
            ("run_id", "generation", "attempt_id"),
            "execution_attempts",
            ("run_id", "generation", "id"),
        ),
        (("owner_id", "run_id"), "research_runs", ("owner_id", "id")),
        (
            (
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "context_id",
                "start_id",
            ),
            "factor_generation_starts",
            ("owner_id", "run_id", "attempt_id", "generation", "context_id", "id"),
        ),
        (
            (
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "start_id",
                "context_id",
                "call_id",
                "assembly_id",
            ),
            "factor_response_assemblies",
            (
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "start_id",
                "context_id",
                "call_id",
                "id",
            ),
        ),
        (
            (
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "start_id",
                "context_id",
                "call_id",
            ),
            "factor_upstream_calls",
            (
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "start_id",
                "context_id",
                "id",
            ),
        ),
        (
            ("owner_id", "factor_version_id", "source_sha256"),
            "factor_versions",
            ("owner_id", "id", "code_sha256"),
        ),
    ],
    "factor_generation_outcomes": [
        (
            (
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "start_id",
                "logical_step",
            ),
            "factor_generation_starts",
            ("owner_id", "run_id", "attempt_id", "generation", "id", "logical_step"),
        ),
        (("owner_id", "factor_version_id"), "factor_versions", ("owner_id", "id")),
        (
            (
                "owner_id",
                "run_id",
                "continuation_source_id",
                "continuation_factor_version_id",
            ),
            "factor_generation_sources",
            ("owner_id", "run_id", "id", "factor_version_id"),
        ),
        (
            ("owner_id", "run_id", "attempt_id", "generation", "start_id"),
            "factor_generation_starts",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
        (
            (
                "owner_id",
                "adoption_object_id",
                "adoption_object_kind",
                "adoption_sha256",
            ),
            "research_objects",
            ("owner_id", "id", "object_kind", "sha256"),
        ),
        (("owner_id", "proposal_object_id"), "research_objects", ("owner_id", "id")),
        (
            ("owner_id", "run_id", "evaluated_source_id", "factor_version_id"),
            "factor_generation_sources",
            ("owner_id", "run_id", "id", "factor_version_id"),
        ),
        (("owner_id", "feedback_object_id"), "research_objects", ("owner_id", "id")),
        (("owner_id", "run_id"), "research_runs", ("owner_id", "id")),
        (
            ("owner_id", "run_id", "adopted_source_id", "adopted_factor_version_id"),
            "factor_generation_sources",
            ("owner_id", "run_id", "id", "factor_version_id"),
        ),
        (
            ("run_id", "generation", "attempt_id"),
            "execution_attempts",
            ("run_id", "generation", "id"),
        ),
    ],
    "factor_generation_model_operations": [
        (
            (
                "owner_id",
                "parse_failure_object_id",
                "parse_failure_object_kind",
                "parse_failure_sha256",
            ),
            "research_objects",
            ("owner_id", "id", "object_kind", "sha256"),
        ),
        (
            (
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "call_id",
                "predecessor_id",
            ),
            "factor_generation_model_operations",
            ("owner_id", "run_id", "attempt_id", "generation", "call_id", "id"),
        ),
        (
            (
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "context_id",
                "start_id",
            ),
            "factor_generation_starts",
            ("owner_id", "run_id", "attempt_id", "generation", "context_id", "id"),
        ),
        (("owner_id", "run_id"), "research_runs", ("owner_id", "id")),
        (
            (
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "operation_id",
                "context_id",
            ),
            "research_model_operations",
            ("owner_id", "run_id", "attempt_id", "generation", "id", "context_id"),
        ),
        (
            ("run_id", "generation", "attempt_id"),
            "execution_attempts",
            ("run_id", "generation", "id"),
        ),
        (
            (
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "start_id",
                "context_id",
                "call_id",
            ),
            "factor_upstream_calls",
            (
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "start_id",
                "context_id",
                "id",
            ),
        ),
    ],
    "factor_response_assembly_parts": [
        (("owner_id", "run_id"), "research_runs", ("owner_id", "id")),
        (
            ("owner_id", "run_id", "attempt_id", "generation", "call_id", "member_id"),
            "factor_generation_model_operations",
            ("owner_id", "run_id", "attempt_id", "generation", "call_id", "id"),
        ),
        (
            (
                "owner_id",
                "response_object_id",
                "response_object_kind",
                "response_sha256",
            ),
            "research_objects",
            ("owner_id", "id", "object_kind", "sha256"),
        ),
        (
            ("owner_id", "run_id", "attempt_id", "generation", "outcome_id"),
            "research_model_outcomes",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
        (
            (
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "call_id",
                "assembly_id",
            ),
            "factor_response_assemblies",
            ("owner_id", "run_id", "attempt_id", "generation", "call_id", "id"),
        ),
        (
            ("run_id", "generation", "attempt_id"),
            "execution_attempts",
            ("run_id", "generation", "id"),
        ),
    ],
    "factor_evaluation_starts": [
        (
            ("owner_id", "run_id", "evaluated_source_id", "factor_version_id"),
            "factor_generation_sources",
            ("owner_id", "run_id", "id", "factor_version_id"),
        ),
        (
            ("owner_id", "run_id", "attempt_id", "generation", "start_id"),
            "factor_generation_starts",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
        (
            ("run_id", "generation", "attempt_id"),
            "execution_attempts",
            ("run_id", "generation", "id"),
        ),
        (("owner_id", "run_id"), "research_runs", ("owner_id", "id")),
    ],
    "factor_evaluation_outcomes": [
        (("owner_id", "snapshot_id"), "feature_snapshots", ("owner_id", "id")),
        (
            ("owner_id", "run_id", "attempt_id", "generation", "evaluation_id"),
            "factor_evaluation_starts",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
        (
            (
                "owner_id",
                "feedback_object_id",
                "feedback_object_kind",
                "feedback_sha256",
            ),
            "research_objects",
            ("owner_id", "id", "object_kind", "sha256"),
        ),
        (
            ("run_id", "generation", "attempt_id"),
            "execution_attempts",
            ("run_id", "generation", "id"),
        ),
        (("owner_id", "run_id"), "research_runs", ("owner_id", "id")),
    ],
    "factor_cutoff_executions": [
        (
            (
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "evaluation_id",
                "start_id",
                "evaluated_source_id",
                "factor_version_id",
            ),
            "factor_evaluation_starts",
            (
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "id",
                "start_id",
                "evaluated_source_id",
                "factor_version_id",
            ),
        ),
        (("owner_id", "run_id"), "research_runs", ("owner_id", "id")),
        (
            ("owner_id", "run_id", "evaluated_source_id", "factor_version_id"),
            "factor_generation_sources",
            ("owner_id", "run_id", "id", "factor_version_id"),
        ),
        (
            ("owner_id", "run_id", "attempt_id", "generation", "start_id"),
            "factor_generation_starts",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
        (
            (
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "candidate_admission_id",
            ),
            "research_process_admissions",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
        (("owner_id", "factor_version_id"), "factor_versions", ("owner_id", "id")),
        (
            ("run_id", "generation", "attempt_id"),
            "execution_attempts",
            ("run_id", "generation", "id"),
        ),
    ],
    "factor_cutoff_outcomes": [
        (
            ("owner_id", "values_object_id", "values_object_kind", "values_sha256"),
            "factor_objects",
            ("owner_id", "id", "object_kind", "sha256"),
        ),
        (
            ("run_id", "generation", "attempt_id"),
            "execution_attempts",
            ("run_id", "generation", "id"),
        ),
        (("owner_id", "run_id"), "research_runs", ("owner_id", "id")),
        (
            (
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "normalizer_admission_id",
            ),
            "research_process_admissions",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
        (
            ("owner_id", "hdf_object_id", "hdf_object_kind", "hdf_sha256"),
            "factor_objects",
            ("owner_id", "id", "object_kind", "sha256"),
        ),
        (
            ("owner_id", "run_id", "attempt_id", "generation", "execution_id"),
            "factor_cutoff_executions",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
    ],
    "factor_research_checkpoints": [
        (("owner_id", "run_id"), "research_runs", ("owner_id", "id")),
        (
            (
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "last_outcome_id",
                "completed_step",
            ),
            "factor_generation_outcomes",
            ("owner_id", "run_id", "attempt_id", "generation", "id", "logical_step"),
        ),
        (
            ("owner_id", "run_id", "spec_id"),
            "factor_research_specs",
            ("owner_id", "run_id", "id"),
        ),
        (
            ("run_id", "generation", "attempt_id"),
            "execution_attempts",
            ("run_id", "generation", "id"),
        ),
    ],
    "factor_research_attempt_bindings": [
        (
            ("run_id", "generation", "attempt_id"),
            "execution_attempts",
            ("run_id", "generation", "id"),
        ),
        (("owner_id", "run_id"), "research_runs", ("owner_id", "id")),
        (
            ("owner_id", "run_id", "resumed_checkpoint_id"),
            "factor_research_checkpoints",
            ("owner_id", "run_id", "id"),
        ),
        (
            ("owner_id", "run_id", "spec_id"),
            "factor_research_specs",
            ("owner_id", "run_id", "id"),
        ),
    ],
    "factor_source_publications": [
        (("owner_id", "run_id"), "research_runs", ("owner_id", "id")),
        (
            ("owner_id", "run_id", "attempt_id", "generation", "call_id"),
            "factor_upstream_calls",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
        (
            ("owner_id", "run_id", "attempt_id", "generation", "start_id"),
            "factor_generation_starts",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
        (
            ("run_id", "generation", "attempt_id"),
            "execution_attempts",
            ("run_id", "generation", "id"),
        ),
        (
            (
                "owner_id",
                "assembly_object_id",
                "assembly_object_kind",
                "assembly_sha256",
            ),
            "research_objects",
            ("owner_id", "id", "object_kind", "sha256"),
        ),
        (
            ("owner_id", "run_id", "attempt_id", "generation", "budget_reservation_id"),
            "research_budget_reservations",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
    ],
    "factor_source_publication_outcomes": [
        (("owner_id", "run_id"), "research_runs", ("owner_id", "id")),
        (
            ("owner_id", "run_id", "attempt_id", "generation", "source_id"),
            "factor_generation_sources",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
        (
            ("owner_id", "run_id", "attempt_id", "generation", "publication_id"),
            "factor_source_publications",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
        (
            ("run_id", "generation", "attempt_id"),
            "execution_attempts",
            ("run_id", "generation", "id"),
        ),
    ],
    "factor_controller_launches": [
        (
            ("run_id", "generation", "attempt_id"),
            "execution_attempts",
            ("run_id", "generation", "id"),
        ),
        (("owner_id", "run_id"), "research_runs", ("owner_id", "id")),
        (
            ("owner_id", "run_id", "attempt_id", "generation", "admission_id"),
            "research_process_admissions",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
        (
            ("owner_id", "run_id", "attempt_id", "generation", "attempt_binding_id"),
            "factor_research_attempt_bindings",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
    ],
    "factor_upstream_call_outcomes": [
        (
            ("owner_id", "run_id", "attempt_id", "generation", "controller_launch_id"),
            "factor_controller_launches",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
        (
            ("owner_id", "run_id", "attempt_id", "generation", "call_id"),
            "factor_upstream_calls",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
        (
            ("run_id", "generation", "attempt_id"),
            "execution_attempts",
            ("run_id", "generation", "id"),
        ),
        (("owner_id", "run_id"), "research_runs", ("owner_id", "id")),
    ],
    "factor_source_read_witnesses": [
        (
            ("owner_id", "run_id", "attempt_id", "generation", "controller_launch_id"),
            "factor_controller_launches",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
        (
            ("owner_id", "source_object_id", "source_object_kind", "source_sha256"),
            "factor_objects",
            ("owner_id", "id", "object_kind", "sha256"),
        ),
        (
            ("owner_id", "run_id", "attempt_id", "generation", "call_id"),
            "factor_upstream_calls",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
        (("owner_id", "run_id"), "research_runs", ("owner_id", "id")),
        (
            ("owner_id", "run_id", "attempt_id", "generation", "assembly_id"),
            "factor_response_assemblies",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
        (
            ("owner_id", "run_id", "attempt_id", "generation", "source_id"),
            "factor_generation_sources",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
        (
            ("run_id", "generation", "attempt_id"),
            "execution_attempts",
            ("run_id", "generation", "id"),
        ),
    ],
    "factor_source_read_parts": [
        (
            ("owner_id", "run_id", "attempt_id", "generation", "assembly_part_id"),
            "factor_response_assembly_parts",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
        (
            (
                "owner_id",
                "response_object_id",
                "response_object_kind",
                "response_sha256",
            ),
            "research_objects",
            ("owner_id", "id", "object_kind", "sha256"),
        ),
        (
            ("owner_id", "run_id", "attempt_id", "generation", "witness_id"),
            "factor_source_read_witnesses",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
        (
            ("run_id", "generation", "attempt_id"),
            "execution_attempts",
            ("run_id", "generation", "id"),
        ),
        (
            ("owner_id", "run_id", "attempt_id", "generation", "outcome_id"),
            "research_model_outcomes",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
        (
            ("owner_id", "run_id", "attempt_id", "generation", "member_id"),
            "factor_generation_model_operations",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
        (("owner_id", "run_id"), "research_runs", ("owner_id", "id")),
    ],
    "factor_generation_observations": [
        (("owner_id", "run_id"), "research_runs", ("owner_id", "id")),
        (
            ("owner_id", "run_id", "attempt_id", "generation", "controller_launch_id"),
            "factor_controller_launches",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
        (
            ("owner_id", "run_id", "attempt_id", "generation", "start_id"),
            "factor_generation_starts",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
        (
            ("run_id", "generation", "attempt_id"),
            "execution_attempts",
            ("run_id", "generation", "id"),
        ),
        (
            (
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "start_id",
                "logical_step",
            ),
            "factor_generation_starts",
            ("owner_id", "run_id", "attempt_id", "generation", "id", "logical_step"),
        ),
    ],
    "factor_generation_boundaries": [
        (
            (
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "observation_id",
                "start_id",
                "logical_step",
            ),
            "factor_generation_observations",
            (
                "owner_id",
                "run_id",
                "attempt_id",
                "generation",
                "id",
                "start_id",
                "logical_step",
            ),
        ),
        (
            ("owner_id", "run_id", "attempt_id", "generation", "controller_launch_id"),
            "factor_controller_launches",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
        (
            ("run_id", "generation", "attempt_id"),
            "execution_attempts",
            ("run_id", "generation", "id"),
        ),
        (("owner_id", "run_id"), "research_runs", ("owner_id", "id")),
    ],
    "factor_candidate_input_inventories": [
        (("owner_id", "run_id"), "research_runs", ("owner_id", "id")),
        (
            ("run_id", "generation", "attempt_id"),
            "execution_attempts",
            ("run_id", "generation", "id"),
        ),
        (
            ("owner_id", "run_id", "attempt_id", "generation", "admission_id"),
            "research_process_admissions",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
        (
            ("owner_id", "run_id", "attempt_id", "generation", "execution_id"),
            "factor_cutoff_executions",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
    ],
    "factor_candidate_input_files": [
        (
            ("owner_id", "publication_id", "object_kind", "sha256"),
            "research_object_publications",
            ("owner_id", "id", "object_kind", "sha256"),
        ),
        (
            ("owner_id", "research_object_id", "object_kind", "sha256"),
            "research_objects",
            ("owner_id", "id", "object_kind", "sha256"),
        ),
        (
            ("owner_id", "run_id", "attempt_id", "generation", "inventory_id"),
            "factor_candidate_input_inventories",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
        (
            ("run_id", "generation", "attempt_id"),
            "execution_attempts",
            ("run_id", "generation", "id"),
        ),
        (
            ("owner_id", "factor_object_id", "object_kind", "sha256"),
            "factor_objects",
            ("owner_id", "id", "object_kind", "sha256"),
        ),
        (("owner_id", "run_id"), "research_runs", ("owner_id", "id")),
        (
            ("owner_id", "run_id", "attempt_id", "generation", "publication_id"),
            "research_object_publications",
            ("owner_id", "run_id", "attempt_id", "generation", "id"),
        ),
    ],
}

INTEGER_FIELDS = {
    "factor_research_specs": (),
    "factor_generation_starts": ("logical_step", "task_index", "generation"),
    "factor_upstream_calls": ("ordinal", "generation"),
    "factor_response_assemblies": ("part_count", "generation"),
    "factor_generation_sources": ("generation",),
    "factor_generation_outcomes": ("logical_step", "generation"),
    "factor_generation_model_operations": ("request_ordinal", "generation"),
    "factor_response_assembly_parts": ("ordinal", "generation"),
    "factor_evaluation_starts": ("expected_cutoffs", "generation"),
    "factor_evaluation_outcomes": ("generation",),
    "factor_cutoff_executions": ("cutoff_ordinal", "generation"),
    "factor_cutoff_outcomes": ("row_count", "generation"),
    "factor_research_checkpoints": ("completed_step", "generation"),
    "factor_research_attempt_bindings": ("generation",),
    "factor_source_publications": ("size_bytes", "peak_bytes", "generation"),
    "factor_source_publication_outcomes": ("generation",),
    "factor_controller_launches": ("generation",),
    "factor_upstream_call_outcomes": ("member_count", "generation"),
    "factor_source_read_witnesses": (
        "source_size_bytes",
        "raw_text_bytes",
        "normalized_json_bytes",
        "selected_first_ordinal",
        "part_count",
        "generation",
    ),
    "factor_source_read_parts": ("ordinal", "content_size_bytes", "generation"),
    "factor_generation_observations": ("logical_step", "generation"),
    "factor_generation_boundaries": ("logical_step", "next_logical_step", "generation"),
    "factor_candidate_input_inventories": ("file_count", "generation"),
    "factor_candidate_input_files": ("ordinal", "size_bytes", "generation"),
}


def _canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def _require(value, reason):
    if not value:
        raise ValueError("RD source closure: " + reason)


def read_closure(connection, maximum_rows, *, revision, maximum_bytes=MAX_BYTES):
    _require(
        capabilities_for(revision).rd_source_closure_schema == SCHEMA,
        "revision capability",
    )
    _require(
        type(maximum_rows) is int
        and maximum_rows > 0
        and type(maximum_bytes) is int
        and maximum_bytes > 0,
        "bounds",
    )
    count = size = 0

    def rows(query):
        nonlocal count, size
        result = connection.execute(
            text(query).execution_options(stream_results=True, yield_per=1),
            {"limit": maximum_rows + 1},
        ).scalars()
        output = []
        try:
            for row in result:
                count += 1
                size += len(_canonical(row))
                _require(count <= maximum_rows and size <= maximum_bytes, "read bounds")
                output.append(row)
        finally:
            result.close()
        return output

    tables = {
        name: rows(f"SELECT to_jsonb(t) FROM {name} t ORDER BY id LIMIT :limit")
        for name in FIELDS
    }
    consumers = rows(
        "SELECT to_jsonb(t) FROM engine_object_reservation_consumers t WHERE consumer_kind IN ('research_object','factor_source') ORDER BY reservation_id LIMIT :limit"
    )
    external = {
        "runs": rows(
            "SELECT to_jsonb(t) FROM research_runs t WHERE purpose='factor_research' AND engine='rdagent' ORDER BY id LIMIT :limit"
        ),
        "datasets": rows(
            "SELECT to_jsonb(t) FROM (SELECT d.id,d.owner_id,d.content_sha256 FROM dataset_versions d WHERE EXISTS (SELECT 1 FROM research_runs r WHERE r.dataset_version_id=d.id AND r.purpose='factor_research' AND r.engine='rdagent') ORDER BY d.id) t LIMIT :limit"
        ),
    }
    closure = {
        "schema_version": SCHEMA,
        "tables": tables,
        "consumer_claims": consumers,
        "external_bindings": external,
    }
    _indexes(closure, maximum_rows, maximum_bytes)
    return closure


def _indexes(closure, maximum_rows, maximum_bytes=MAX_BYTES):
    _require(type(maximum_rows) is int and maximum_rows > 0, "row bound")
    _require(
        type(closure) is dict
        and set(closure)
        == {"schema_version", "tables", "consumer_claims", "external_bindings"}
        and closure["schema_version"] == SCHEMA,
        "schema",
    )
    _require(
        type(closure["tables"]) is dict and set(closure["tables"]) == set(FIELDS),
        "table inventory",
    )
    _require(
        type(closure["external_bindings"]) is dict
        and set(closure["external_bindings"]) == set(EXTERNAL_FIELDS),
        "external inventory",
    )
    _require(len(_canonical(closure)) <= maximum_bytes, "byte bound")
    result = {}
    count = 0
    for name, fields in {
        **FIELDS,
        **EXTERNAL_FIELDS,
        "consumer_claims": CONSUMER_FIELDS,
    }.items():
        rows = closure["tables"].get(
            name,
            closure["external_bindings"].get(
                name, closure["consumer_claims"] if name == "consumer_claims" else None
            ),
        )
        _require(type(rows) is list, "rows")
        count += len(rows)
        _require(count <= maximum_rows, "row bound")
        key = "reservation_id" if name == "consumer_claims" else "id"
        index = {}
        for row in rows:
            _require(
                type(row) is dict and set(row) == set(fields.split()),
                "row fields " + name,
            )
            for field in ("owner_id", key):
                _require(
                    type(row[field]) is str and str(UUID(row[field])) == row[field],
                    "UUID",
                )
            _require(row[key] not in index, "duplicate identity")
            for field in INTEGER_FIELDS.get(name, ()):
                _require(
                    row[field] is None or type(row[field]) is int,
                    "integer column " + field,
                )
            if "generation" in row:
                _require(
                    type(row["generation"]) is int and row["generation"] > 0,
                    "generation",
                )
            if "created_at" in row:
                _require(
                    datetime.fromisoformat(row["created_at"]).utcoffset() is not None,
                    "timestamp",
                )
            index[row[key]] = row
        result[name] = index
    _require(
        all(not result[name] for name in PHASE_EMPTY),
        "unsupported outcomes/adoption/checkpoint",
    )
    return result


def validate_closure(
    closure, research_closure, factor_closure, read_bytes, maximum_rows
):
    """Cross-close already independently checked shared closures and actual bytes.

    read_bytes(key, expected_sha256, maximum_bytes) must use the independently
    inventoried bounded no-follow reader. We still check exact byte count/hash.
    Pending source publications may lack final files/outcomes; their reservations
    remain charged and global inventory preserves every physical orphan.
    """
    idx = _indexes(closure, maximum_rows)
    _require(
        research_closure.get("schema_version")
        == "mineagent.research-restore-closure.v4",
        "research revision",
    )
    _require(
        factor_closure.get("schema_version")
        in {
            "mineagent.factor-restore-closure.v1",
            "mineagent.factor-restore-closure.v2",
        },
        "factor revision",
    )
    shared = {}
    for name, rows in {
        **research_closure["tables"],
        **factor_closure["tables"],
        "attempts": research_closure["external_bindings"]["attempts"],
    }.items():
        key = {
            "research_object_outcomes": "publication_id",
            "research_process_states": "admission_id",
        }.get(name, "id")
        _require(type(rows) is list and len(rows) <= maximum_rows, "shared bound")
        shared[name] = {r[key]: r for r in rows}
        _require(len(shared[name]) == len(rows), "shared duplicate")
    allidx = {
        **shared,
        **idx,
        "execution_attempts": shared["attempts"],
        "research_runs": idx["runs"],
    }
    for table, keys in UNIQUE_KEYS.items():
        for columns in keys:
            seen = set()
            for row in idx[table].values():
                values = tuple(row[k] for k in columns)
                if None not in values:
                    _require(values not in seen, "unique " + table)
                    seen.add(values)
    for table, references in FOREIGN_KEYS.items():
        for columns, target, target_columns in references:
            if not idx[table]:
                continue
            _require(target in allidx, "missing referenced table " + target)
            targets = {
                tuple(row[k] for k in target_columns) for row in allidx[target].values()
            }
            for row in idx[table].values():
                values = tuple(row[k] for k in columns)
                if None not in values:
                    _require(
                        values in targets, "foreign tuple " + table + " -> " + target
                    )

    def link(row, field, table, *, scope=True):
        other = allidx[table].get(row[field])
        _require(other is not None, "missing " + table)
        _require(other["owner_id"] == row["owner_id"], "owner " + table)
        if scope and "run_id" in other:
            _require(
                all(other[k] == row[k] for k in SCOPE if k in other and k in row),
                "scope " + table,
            )
        return other

    def exact(rows, predicate, reason):
        found = [r for r in rows.values() if predicate(r)]
        _require(len(found) == 1, reason)
        return found[0]

    def raw(row, kind, *, factor=False):
        _require(row["object_kind"] == kind, "object kind")
        limit = 131072 if factor else 1048576
        _require(
            type(row["size_bytes"]) is int and 1 <= row["size_bytes"] <= limit,
            "object size",
        )
        suffix = "py" if factor else "json"
        expected = (
            f"factors/{row['owner_id']}/objects/{row['sha256']}.{suffix}"
            if factor
            else f"research-objects/{row['owner_id']}/by-kind/{kind}/{row['sha256']}.json"
        )
        _require(row["storage_key"] == expected, "object path")
        data = read_bytes(expected, row["sha256"], limit)
        _require(
            type(data) is bytes
            and len(data) == row["size_bytes"]
            and hashlib.sha256(data).hexdigest() == row["sha256"],
            "actual object bytes",
        )
        return data

    configs = {}
    for run in idx["runs"].values():
        _require(
            (run["purpose"], run["engine"]) == ("factor_research", "rdagent"),
            "run identity",
        )
        from packages.rdagent_adapter.persisted_identity import require_source_identity

        values = dict(run)
        values["dataset_version_id"] = UUID(values["dataset_version_id"])
        config = require_source_identity(SimpleNamespace(**values))
        dataset = link(run, "dataset_version_id", "datasets", scope=False)
        _require(
            dataset["content_sha256"] == config.input.dataset.content_sha256,
            "dataset digest",
        )
        control = exact(
            shared["research_budget_controls"],
            lambda c, run=run: (
                c["run_id"] == run["id"] and c["owner_id"] == run["owner_id"]
            ),
            "run budget",
        )
        _require(control["limits"] == config.budget.model_dump(), "config budget")
        configs[run["id"]] = config
    for name in FIELDS:
        for row in idx[name].values():
            link(row, "run_id", "runs")
            if "attempt_id" in row:
                link(row, "attempt_id", "attempts")
    for spec in idx["factor_research_specs"].values():
        config = configs[spec["run_id"]]
        _require(
            spec["config"] == config.model_dump(mode="json")
            and spec["config_sha256"] == config.sha256
            and spec["input_sha256"] == config.input.sha256,
            "spec identity",
        )
    _require(len(idx["factor_research_specs"]) == len(idx["runs"]), "one spec per run")
    for binding in idx["factor_research_attempt_bindings"].values():
        link(binding, "spec_id", "factor_research_specs")
        _require(binding["resumed_checkpoint_id"] is None, "fresh only")
    for launch in idx["factor_controller_launches"].values():
        binding = link(launch, "attempt_binding_id", "factor_research_attempt_bindings")
        spec = link(binding, "spec_id", "factor_research_specs")
        bootstrap = RDOperationalBootstrap.model_validate_json(
            json.dumps(launch["bootstrap"])
        )
        expected = RDOperationalBootstrap(
            **{k: launch[k] for k in SCOPE},
            spec_id=spec["id"],
            attempt_binding_id=binding["id"],
            config=configs[launch["run_id"]],
            config_sha256=spec["config_sha256"],
            input_sha256=spec["input_sha256"],
        )
        _require(
            bootstrap == expected and launch["bootstrap_sha256"] == bootstrap.sha256,
            "controller bootstrap",
        )
        admission = link(launch, "admission_id", "research_process_admissions")
        _require(
            (
                admission["role"],
                admission["profile"],
                admission["image_id"],
                admission["input_sha256"],
                admission["logical_operation_id"],
            )
            == (
                "rd_orchestrator",
                "mineagent.rdagent-orchestrator.v1",
                bootstrap.config.runtime.orchestrator_image,
                bootstrap.sha256,
                binding["id"],
            ),
            "controller admission",
        )
    starts = idx["factor_generation_starts"]
    for start in starts.values():
        spec = link(start, "spec_id", "factor_research_specs")
        config = configs[start["run_id"]]
        exact(
            idx["factor_research_attempt_bindings"],
            lambda b, start=start: (
                all(b[k] == start[k] for k in SCOPE)
                and b["spec_id"] == start["spec_id"]
            ),
            "start attempt binding",
        )
        _require(
            start["task_index"] == 0
            and type(start["task_index"]) is int
            and type(start["logical_step"]) is int
            and 0 <= start["logical_step"] < config.max_iterations,
            "step",
        )
        # Phase018 has no completed prior-generation/adoption rows.
        _require(
            start["logical_step"] == 0 and start["parent_factor_version_id"] is None,
            "initial source-only lineage",
        )
        context = link(start, "context_id", "research_operation_contexts")
        expected = {
            "schema_version": "mineagent.rdagent-context.v1",
            "spec_sha256": spec["config_sha256"],
            "input_sha256": spec["input_sha256"],
            "logical_step": start["logical_step"],
            "task_index": 0,
        }
        _require(
            context["context"] == expected
            and context["context_sha256"] == digest(expected),
            "context",
        )
        reservation = link(
            start, "mutation_reservation_id", "research_budget_reservations"
        )
        _require(
            reservation["purpose"] == "engine_mutation"
            and reservation["amounts"] == BudgetVector(mutation_slots=1).model_dump(),
            "mutation",
        )
    _require(
        len({s["mutation_reservation_id"] for s in starts.values()}) == len(starts),
        "unique mutation",
    )
    members_by_call = defaultdict(list)
    for call in idx["factor_upstream_calls"].values():
        start = link(call, "start_id", "factor_generation_starts")
        _require(
            call["context_id"] == start["context_id"]
            and call["phase"]
            in {
                "generation",
                "output_format",
                "critique",
                "final_decision",
                "knowledge_query",
                "knowledge_update",
            }
            and call["kind"] in {"chat", "embedding"},
            "call",
        )
        _require(
            call["kind"] != "embedding"
            or call["phase"] in {"knowledge_query", "knowledge_update"},
            "embedding phase",
        )
    call_groups = defaultdict(list)
    for call in idx["factor_upstream_calls"].values():
        call_groups[(call["start_id"], call["phase"])].append(call["ordinal"])
    for ordinals in call_groups.values():
        _require(
            len(ordinals) <= 1000 and sorted(ordinals) == list(range(len(ordinals))),
            "call ordinal closure",
        )
    for member in idx["factor_generation_model_operations"].values():
        call = link(member, "call_id", "factor_upstream_calls")
        _require(
            (member["start_id"], member["context_id"])
            == (call["start_id"], call["context_id"]),
            "member scope",
        )
        operation = link(member, "operation_id", "research_model_operations")
        protocol = "mineagent.rdagent-" + call["kind"] + ".v1"
        request = parse_request(protocol, operation["request"])
        _require(
            member["protocol"] == operation["protocol"] == protocol
            and request_sha256(protocol, request) == operation["request_sha256"]
            and operation["context_id"] == call["context_id"],
            "request identity",
        )
        _require(
            operation["purpose"]
            == (
                "embedding"
                if call["kind"] == "embedding"
                else "continuation"
                if member["relation"] == "continuation"
                else "chat"
            ),
            "request purpose",
        )
        obj = link(operation, "request_object_id", "research_objects", scope=False)
        _require(
            raw(obj, "model_request") == broker_bytes(protocol, request),
            "request bytes",
        )
        members_by_call[call["id"]].append(member)
    for members in members_by_call.values():
        members.sort(key=lambda r: r["request_ordinal"])
        for n, m in enumerate(members):
            _require(
                type(m["request_ordinal"]) is int
                and m["request_ordinal"] == n
                and m["predecessor_id"] == (members[n - 1]["id"] if n else None),
                "member sequence",
            )
            _require(
                m["relation"]
                in ({"initial"} if n == 0 else {"continuation", "semantic_repair"}),
                "member relation",
            )
            # Revision018 has no parse_failure object kind admission.
            _require(
                m["relation"] != "semantic_repair"
                and all(
                    m[k] is None
                    for k in (
                        "parse_failure_object_id",
                        "parse_failure_object_kind",
                        "parse_failure_sha256",
                    )
                ),
                "unregistered repair receipt",
            )

    def completed(member):
        op = link(member, "operation_id", "research_model_operations")
        _require(op["state"] == "completed", "completed operation")
        transports = sorted(
            [
                t
                for t in shared["research_model_transports"].values()
                if t["operation_id"] == op["id"]
            ],
            key=lambda t: t["ordinal"],
        )
        _require(1 <= len(transports) <= 256, "transport count")
        outcome = None
        for n, t in enumerate(transports, 1):
            _require(
                all(t[k] == member[k] for k in SCOPE) and t["ordinal"] == n,
                "transport sequence",
            )
            outcome = exact(
                shared["research_model_outcomes"],
                lambda o, tid=t["id"]: o["transport_id"] == tid,
                "transport outcome",
            )
            held = link(t, "reservation_id", "research_budget_reservations")
            settlement = exact(
                shared["research_budget_settlements"],
                lambda s, rid=held["id"]: s["reservation_id"] == rid,
                "transport settlement",
            )
            state = "completed" if n == len(transports) else "failed"
            _require(
                all(outcome[k] == member[k] == settlement[k] for k in SCOPE)
                and t["state"] == outcome["outcome"] == state,
                "transport state",
            )
            _require(
                held["purpose"] == "model_transport"
                and held["state"] == "settled"
                and settlement["outcome"] == "measured"
                and settlement["charge"] == outcome["usage"],
                "transport accounting",
            )
            charge = BudgetVector.model_validate(outcome["usage"])
            cap = BudgetVector.model_validate(held["amounts"])
            _require(
                charge.calls == cap.calls == 1 and cap.covers(charge),
                "transport charge",
            )
        obj = link(outcome, "response_object_id", "research_objects", scope=False)
        _require(
            outcome["response_object_kind"] == "model_response"
            and outcome["response_sha256"] == obj["sha256"],
            "response reference",
        )
        data = raw(obj, "model_response")
        response_type = (
            RDChatResponse
            if op["protocol"] == "mineagent.rdagent-chat.v1"
            else RDEmbeddingResponse
        )
        response = response_type.model_validate_json(data)
        validate_response(
            op["protocol"], parse_request(op["protocol"], op["request"]), response
        )
        _require(
            data == broker_bytes(op["protocol"], response),
            "response canonical bytes",
        )
        return outcome, response

    for members in members_by_call.values():
        for n, member in enumerate(members):
            if member["relation"] == "continuation":
                previous_response = completed(members[n - 1])[1]
                _require(
                    isinstance(previous_response, RDChatResponse)
                    and previous_response.finish_reason == "length",
                    "continuation authorization",
                )
    for closed in idx["factor_upstream_call_outcomes"].values():
        call = link(closed, "call_id", "factor_upstream_calls")
        link(closed, "controller_launch_id", "factor_controller_launches")
        members = members_by_call.get(closed["call_id"], [])
        _require(
            type(closed["member_count"]) is int
            and closed["member_count"] == len(members) <= 60
            and closed["member_ids"] == [m["id"] for m in members],
            "closed members",
        )
        results = [completed(m) for m in members]
        _require(
            closed["response_outcome_ids"] == [o["id"] for o, _ in results],
            "closed responses",
        )
        _require(closed["status"] in {"completed", "aborted"}, "closed status")
        if closed["status"] == "completed":
            _require(bool(results), "empty completion")
            if call["kind"] == "chat":
                _require(
                    results[-1][1].finish_reason == "stop", "successful chat boundary"
                )
    claims = idx["consumer_claims"]
    expectedclaims = {}
    for table, kind in [
        ("research_object_publications", "research_object"),
        ("factor_source_publications", "factor_source"),
    ]:
        for pub in allidx[table].values():
            rid = pub["budget_reservation_id"]
            _require(rid not in expectedclaims, "double reservation consumer")
            expectedclaims[rid] = {
                "reservation_id": rid,
                **{k: pub[k] for k in SCOPE},
                "consumer_kind": kind,
                "consumer_id": pub["id"],
            }
    _require(claims == expectedclaims, "consumer arbitration")
    for pub in idx["factor_source_publications"].values():
        start = link(pub, "start_id", "factor_generation_starts")
        call = link(pub, "call_id", "factor_upstream_calls")
        _require(
            call["start_id"] == start["id"]
            and call["phase"] == "generation"
            and call["kind"] == "chat",
            "publication call",
        )
        assemblyobj = link(pub, "assembly_object_id", "research_objects", scope=False)
        document = ResponseAssemblyDocument.model_validate_json(
            raw(assemblyobj, "factor_response_assembly")
        )
        _require(
            pub["assembly_object_kind"] == "factor_response_assembly"
            and pub["assembly_sha256"] == assemblyobj["sha256"]
            and document.call_id == UUID(call["id"])
            and document.source_sha256 == pub["source_sha256"]
            and len(document.source.encode()) == pub["size_bytes"]
            and pub["peak_bytes"] == pub["size_bytes"] * 2 + 8192,
            "publication content",
        )
        closed = exact(
            idx["factor_upstream_call_outcomes"],
            lambda c, pub=pub: c["call_id"] == pub["call_id"],
            "publication closed call",
        )
        _require(closed["status"] == "completed", "pending source call incomplete")
        launch = link(closed, "controller_launch_id", "factor_controller_launches")
        _require(
            str(document.orchestrator_admission_id) == launch["admission_id"],
            "publication controller",
        )
        members = members_by_call[call["id"]]
        first = max(n for n, m in enumerate(members) if m["relation"] != "continuation")
        selected = members[first:]
        results = [completed(m) for m in selected]
        parsed = parse_selected_responses(tuple(response for _, response in results))
        _require(
            document.source == parsed.source
            and document.source_sha256 == parsed.source_sha256,
            "pending source actual extraction",
        )
        expected_parts = [
            {
                "member_id": m["id"],
                "outcome_id": o["id"],
                "response_object_id": o["response_object_id"],
                "response_sha256": o["response_sha256"],
            }
            for m, (o, _) in zip(selected, results, strict=True)
        ]
        _require(
            [part.model_dump(mode="json") for part in document.parts] == expected_parts,
            "pending source selected parts",
        )
        reservation = link(pub, "budget_reservation_id", "research_budget_reservations")
        if not any(
            o["publication_id"] == pub["id"]
            for o in idx["factor_source_publication_outcomes"].values()
        ):
            _require(
                reservation["state"] in {"reserved", "uncertain"},
                "pending source still reserved",
            )
        _require(
            reservation["purpose"] == "object_storage"
            and reservation["logical_key"] == "rdsource:" + start["id"]
            and reservation["amounts"]
            == BudgetVector(storage_bytes=pub["peak_bytes"]).model_dump(),
            "source reservation",
        )
    for source in idx["factor_generation_sources"].values():
        _validate_source(
            source,
            idx,
            shared,
            configs,
            link,
            exact,
            raw,
            completed,
            members_by_call,
            read_bytes,
        )
    _require(
        {
            v["id"]
            for v in shared["factor_versions"].values()
            if v["source_kind"] == "rdagent"
        }
        == {s["factor_version_id"] for s in idx["factor_generation_sources"].values()},
        "unbound RD factor version",
    )
    _validate_pending(idx, shared, configs, link)
    return True


def _validate_source(
    source,
    idx,
    shared,
    configs,
    link,
    exact,
    raw,
    completed,
    members_by_call,
    read_bytes,
):
    start = link(source, "start_id", "factor_generation_starts")
    call = link(source, "call_id", "factor_upstream_calls")
    assembly = link(source, "assembly_id", "factor_response_assemblies")
    _require(
        call["phase"] == "generation"
        and call["kind"] == "chat"
        and all(
            r["start_id"] == start["id"] and r["context_id"] == start["context_id"]
            for r in (call, assembly, source)
        )
        and assembly["call_id"] == call["id"],
        "source generation",
    )
    closed = exact(
        idx["factor_upstream_call_outcomes"],
        lambda r: r["call_id"] == call["id"],
        "closed source call",
    )
    _require(closed["status"] == "completed", "source incomplete")
    witness = exact(
        idx["factor_source_read_witnesses"],
        lambda r: r["source_id"] == source["id"],
        "source witness",
    )
    _require(
        all(witness[k] == source[k] for k in SCOPE)
        and witness["assembly_id"] == assembly["id"]
        and witness["call_id"] == call["id"]
        and witness["controller_launch_id"] == closed["controller_launch_id"],
        "witness scope",
    )
    _require(
        witness["parser_profile"] == "mineagent.rdagent-source-parser.v1"
        and witness["parser_source_sha256"] == UPSTREAM_FRAGMENT_SHA256,
        "pinned parser",
    )
    launch = link(witness, "controller_launch_id", "factor_controller_launches")
    members = members_by_call[call["id"]]
    first = max(i for i, m in enumerate(members) if m["relation"] != "continuation")
    selected = members[first:]
    _require(
        1 <= len(selected) <= 6
        and witness["selected_first_ordinal"] == first
        and witness["part_count"] == assembly["part_count"] == len(selected),
        "selected chain",
    )
    parts = sorted(
        [
            r
            for r in idx["factor_response_assembly_parts"].values()
            if r["assembly_id"] == assembly["id"]
        ],
        key=lambda r: r["ordinal"],
    )
    readparts = sorted(
        [
            r
            for r in idx["factor_source_read_parts"].values()
            if r["witness_id"] == witness["id"]
        ],
        key=lambda r: r["ordinal"],
    )
    _require(len(parts) == len(readparts) == len(selected), "parts cardinality")
    responses = []
    for n, (member, part, observed) in enumerate(
        zip(selected, parts, readparts, strict=True)
    ):
        outcome, response = completed(member)
        responses.append(response)
        _require(all(part[k] == observed[k] == source[k] for k in SCOPE), "part scope")
        expected = {
            "member_id": member["id"],
            "outcome_id": outcome["id"],
            "response_object_id": outcome["response_object_id"],
            "response_object_kind": "model_response",
            "response_sha256": outcome["response_sha256"],
            "ordinal": n,
        }
        _require(
            all(part[k] == observed[k] == v for k, v in expected.items())
            and part["call_id"] == call["id"]
            and observed["assembly_part_id"] == part["id"],
            "part relation",
        )
        _require(
            observed["finish_reason"]
            == response.finish_reason
            == ("stop" if n == len(selected) - 1 else "length")
            and observed["content_sha256"]
            == hashlib.sha256(response.content.encode()).hexdigest()
            and observed["content_size_bytes"] == len(response.content.encode()),
            "retained response witness",
        )
        op = link(member, "operation_id", "research_model_operations")
        _require(
            op["request"]["response_format"] == {"type": "json_object"},
            "source JSON mode",
        )
    parsed = parse_selected_responses(tuple(responses))
    version = link(source, "factor_version_id", "factor_versions", scope=False)
    obj = link(version, "code_object_id", "factor_objects", scope=False)
    data = raw(obj, "code", factor=True)
    expected_header = {
        "source_sha256": parsed.source_sha256,
        "source_size_bytes": parsed.source_size_bytes,
        "source_object_id": obj["id"],
        "source_object_kind": "code",
        "raw_text_sha256": parsed.raw_text_sha256,
        "raw_text_bytes": len(parsed.raw_text.encode()),
        "normalized_json_sha256": parsed.normalized_json_sha256,
        "normalized_json_bytes": len(parsed.normalized_json.encode()),
    }
    _require(
        all(witness[k] == v for k, v in expected_header.items()),
        "parsed witness content",
    )
    _require(
        data == parsed.source.encode()
        and version["code"] == parsed.source
        and version["code_sha256"] == source["source_sha256"] == parsed.source_sha256
        and version["source_kind"] == "rdagent"
        and version["parent_version_id"] == start["parent_factor_version_id"]
        and version["source_event_key"] == "rdagent:" + start["id"],
        "source code/version",
    )
    definition = research_definition(configs[source["run_id"]], parsed.source_sha256)
    _require(
        version["definition"] == definition.model_dump(mode="json")
        and version["definition_sha256"] == definition.sha256,
        "factor definition",
    )
    assemblyobj = link(assembly, "object_id", "research_objects", scope=False)
    document = ResponseAssemblyDocument.model_validate_json(
        raw(assemblyobj, "factor_response_assembly")
    )
    _require(
        assembly["object_kind"] == "factor_response_assembly"
        and assembly["object_sha256"] == assemblyobj["sha256"]
        and str(document.call_id) == call["id"]
        and str(document.orchestrator_admission_id) == launch["admission_id"]
        and document.source == parsed.source
        and document.source_sha256 == parsed.source_sha256,
        "assembly document",
    )
    _require(
        [p.model_dump(mode="json") for p in document.parts]
        == [
            {
                k: part[k]
                for k in (
                    "member_id",
                    "outcome_id",
                    "response_object_id",
                    "response_sha256",
                )
            }
            for part in parts
        ],
        "assembly document parts",
    )
    _require(
        version["source_provenance"]
        == {
            "schema_version": "mineagent.rdagent-source.v1",
            "run_id": source["run_id"],
            "start_id": start["id"],
            "call_id": call["id"],
            "assembly_sha256": assembly["object_sha256"],
            "explanation_origin": "task_declaration",
        },
        "source provenance",
    )
    publication = exact(
        idx["factor_source_publications"],
        lambda r: r["start_id"] == start["id"],
        "source publication",
    )
    expected = {
        "call_id": call["id"],
        "assembly_object_id": assembly["object_id"],
        "assembly_object_kind": assembly["object_kind"],
        "assembly_sha256": assembly["object_sha256"],
        "source_sha256": parsed.source_sha256,
        "size_bytes": len(data),
        "factor_id": version["factor_id"],
        "factor_version_id": version["id"],
    }
    _require(
        all(publication[k] == v for k, v in expected.items()), "publication linkage"
    )
    outcome = exact(
        idx["factor_source_publication_outcomes"],
        lambda r: r["source_id"] == source["id"],
        "source publication outcome",
    )
    _require(
        outcome["publication_id"] == publication["id"]
        and all(outcome[k] == source[k] for k in SCOPE),
        "publication outcome scope",
    )
    # Reproduce fixed journal bytes without importing transaction/services code.
    fields = {
        "schema_version": "mineagent.rdagent-source-publication.v1",
        **{
            k: publication[k]
            for k in (
                "id",
                "owner_id",
                "run_id",
                "attempt_id",
                "start_id",
                "call_id",
                "assembly_object_id",
                "budget_reservation_id",
                "factor_id",
                "factor_version_id",
                "generation",
                "assembly_sha256",
                "source_sha256",
                "size_bytes",
                "peak_bytes",
            )
        },
    }
    journal = canonical_bytes(fields)
    sha = hashlib.sha256(journal).hexdigest()
    _require(
        outcome["journal_sha256"] == sha
        and read_bytes(f"factors/{source['owner_id']}/objects/{sha}.json", sha, 4096)
        == journal,
        "source journal bytes",
    )
    header = {
        k: v
        for k, v in witness.items()
        if k not in {"id", "created_at", *SCOPE, "witness_sha256"}
    }
    part_documents = [
        {
            k: v
            for k, v in p.items()
            if k not in {"id", "created_at", *SCOPE, "witness_id"}
        }
        for p in readparts
    ]
    _require(
        witness["witness_sha256"]
        == digest(
            {
                "schema_version": "mineagent.source-read-witness.v1",
                "scope": {k: source[k] for k in SCOPE},
                "header": header,
                "parts": part_documents,
            }
        ),
        "witness metadata digest",
    )


def _validate_pending(idx, shared, configs, link):
    # Every metadata record is attached, even when no final source/outcome exists.
    for row in idx["factor_response_assemblies"].values():
        link(row, "start_id", "factor_generation_starts")
        link(row, "call_id", "factor_upstream_calls")
        _require(
            sum(
                s["assembly_id"] == row["id"]
                for s in idx["factor_generation_sources"].values()
            )
            == 1,
            "unbound assembly",
        )
    for row in idx["factor_response_assembly_parts"].values():
        link(row, "assembly_id", "factor_response_assemblies")
        link(row, "member_id", "factor_generation_model_operations")
    for row in idx["factor_source_read_witnesses"].values():
        link(row, "source_id", "factor_generation_sources")
    for row in idx["factor_source_read_parts"].values():
        link(row, "witness_id", "factor_source_read_witnesses")
    for row in idx["factor_source_publication_outcomes"].values():
        link(row, "publication_id", "factor_source_publications")
        link(row, "source_id", "factor_generation_sources")
    for evaluation in idx["factor_evaluation_starts"].values():
        source = link(evaluation, "evaluated_source_id", "factor_generation_sources")
        _require(
            (evaluation["start_id"], evaluation["factor_version_id"])
            == (source["start_id"], source["factor_version_id"])
            and type(evaluation["expected_cutoffs"]) is int
            and 1 <= evaluation["expected_cutoffs"] <= 4096,
            "evaluation source",
        )
        snapshot = SnapshotInput.model_validate_json(
            json.dumps(evaluation["snapshot_input"])
        )
        expected = research_snapshot_input(
            configs[evaluation["run_id"]],
            factor_version_id=UUID(source["factor_version_id"]),
            code_sha256=source["source_sha256"],
            exposure_history=snapshot.exposure_history,
        )
        _require(
            snapshot == expected and evaluation["input_sha256"] == snapshot.sha256,
            "evaluation input",
        )
    for cutoff in idx["factor_cutoff_executions"].values():
        evaluation = link(cutoff, "evaluation_id", "factor_evaluation_starts")
        _require(
            all(
                cutoff[k] == evaluation[k]
                for k in ("start_id", "evaluated_source_id", "factor_version_id")
            )
            and type(cutoff["cutoff_ordinal"]) is int
            and 0 <= cutoff["cutoff_ordinal"] < evaluation["expected_cutoffs"],
            "cutoff evaluation",
        )
        prefix = PrefixManifest.model_validate_json(json.dumps(cutoff["projection"]))
        config = configs[cutoff["run_id"]]
        _require(
            prefix.sha256 == cutoff["projection_sha256"]
            and prefix.snapshot_input_sha256 == evaluation["input_sha256"]
            and (
                prefix.candidate_image,
                prefix.normalizer_image,
                prefix.candidate_profile,
                prefix.normalizer_profile,
            )
            == (
                config.runtime.candidate_image,
                config.runtime.normalizer_image,
                config.runtime.candidate_profile,
                config.runtime.normalizer_profile,
            ),
            "cutoff projection",
        )
        _require(
            prefix.market_timezone == config.input.market_timezone
            and prefix.calendar_sha256 == config.input.calendar_sha256
            and prefix.input_fields == config.input.input_fields
            and prefix.preprocessing == config.input.preprocessing
            and prefix.lookback_prior_sessions == config.input.lookback_sessions
            and prefix.lag_sessions == config.input.lag_sessions
            and prefix.membership_sha256
            == config.input.calculation_universe.membership_sha256
            and prefix.membership_available_at
            == config.input.calculation_universe.membership_available_at
            and tuple(x.listing_id for x in prefix.listings)
            == config.input.calculation_universe.listing_ids
            and config.input.window.start
            <= prefix.output_session
            < config.input.window.end
            and prefix.max_output_rows == config.max_output_rows_per_cutoff
            and prefix.max_output_bytes == config.max_output_bytes_per_cutoff,
            "cutoff frozen input",
        )
        admission = link(
            cutoff, "candidate_admission_id", "research_process_admissions"
        )
        _require(
            admission["role"] == "factor_cutoff_candidate"
            and admission["logical_operation_id"] == cutoff["id"]
            and admission["image_id"] == config.runtime.candidate_image
            and admission["profile"] == config.runtime.candidate_profile
            and admission["ipc_policy"] == "none"
            and admission["ticket_sha256"] is None,
            "candidate binding",
        )
    # Prefix input object kinds are not registered in018. Empty inventories are
    # preserved; a future revision must implement the byte-reader codec first.
    _require(
        not idx["factor_candidate_input_inventories"]
        and not idx["factor_candidate_input_files"],
        "unregistered prefix input object kinds",
    )


MAX_MANIFEST_KEYS = 100_000


def manifest_keys(closure, factor_closure):
    """Bounded possible retained raw-byte paths; absence is not publication proof.

    Includes pending publication source/journal names so conservative orphan bytes
    remain in retention's pinned cache. The caller scans independently inventoried
    files; it must not assume every possible key exists. Research object manifests
    remain the shared research closure's responsibility. No native suffix allowed.
    """
    import re

    idx = _indexes(closure, MAX_MANIFEST_KEYS)
    _require(
        factor_closure.get("schema_version")
        in {
            "mineagent.factor-restore-closure.v1",
            "mineagent.factor-restore-closure.v2",
        },
        "manifest factor schema",
    )
    objects = factor_closure["tables"]["factor_objects"]
    _require(
        type(objects) is list and len(objects) <= MAX_MANIFEST_KEYS,
        "manifest objects bound",
    )
    by_id = {obj["id"]: obj for obj in objects}
    _require(len(by_id) == len(objects), "manifest duplicate objects")
    result = set()

    def key(owner, sha, suffix):
        _require(type(owner) is str and str(UUID(owner)) == owner, "manifest owner")
        _require(
            type(sha) is str and re.fullmatch("[a-f0-9]{64}", sha) is not None,
            "manifest digest",
        )
        result.add(f"factors/{owner}/objects/{sha}.{suffix}")
        _require(len(result) <= MAX_MANIFEST_KEYS, "manifest key bound")

    for pub in idx["factor_source_publications"].values():
        key(pub["owner_id"], pub["source_sha256"], "py")
        fields = {
            "schema_version": "mineagent.rdagent-source-publication.v1",
            **{
                name: pub[name]
                for name in (
                    "id",
                    "owner_id",
                    "run_id",
                    "attempt_id",
                    "start_id",
                    "call_id",
                    "assembly_object_id",
                    "budget_reservation_id",
                    "factor_id",
                    "factor_version_id",
                    "generation",
                    "assembly_sha256",
                    "source_sha256",
                    "size_bytes",
                    "peak_bytes",
                )
            },
        }
        journal = canonical_bytes(fields)
        _require(len(journal) <= 4096, "manifest journal bound")
        key(pub["owner_id"], hashlib.sha256(journal).hexdigest(), "json")
    for witness in idx["factor_source_read_witnesses"].values():
        obj = by_id.get(witness["source_object_id"])
        _require(
            obj is not None
            and obj["owner_id"] == witness["owner_id"]
            and obj["object_kind"] == witness["source_object_kind"] == "code"
            and obj["sha256"] == witness["source_sha256"],
            "manifest witness object",
        )
        key(obj["owner_id"], obj["sha256"], "py")
        _require(
            obj["storage_key"]
            == f"factors/{obj['owner_id']}/objects/{obj['sha256']}.py",
            "manifest code path",
        )
    return result
