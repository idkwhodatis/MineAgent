"""Exact, no-skip hosted acceptance for neutral infrastructure, not engine loops.

Only fixed test identities and outcome summaries leave the disposable runner.
The independent legacy application/Compose browser workflows remain mandatory.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from types import MappingProxyType
from xml.etree import ElementTree as ET

from script.ci_process_owner import ProcessOwner
from script.summarize_ci_junit import summarize

MAX_REPORT_BYTES = 20_000_000
PROFILE_SECONDS = 1200
# Closed inventory is integrated with the real test owners before publication.
# Collection and unit substitutes cannot satisfy any real profile.
REQUIRED = MappingProxyType(
    {
        "postgres": (
            "tests/integration/test_structured_factor_persistence.py::test_structured_raw_float_commitment_actual_pg[0.0]",
            "tests/integration/test_structured_factor_persistence.py::test_structured_raw_float_commitment_actual_pg[-0.0]",
            "tests/integration/test_structured_factor_persistence.py::test_structured_raw_float_commitment_actual_pg[1.0]",
            "tests/integration/test_structured_factor_persistence.py::test_structured_raw_float_commitment_actual_pg[-1.0]",
            "tests/integration/test_structured_factor_persistence.py::test_structured_raw_float_commitment_actual_pg[1e-07]",
            "tests/integration/test_structured_factor_persistence.py::test_structured_raw_float_commitment_actual_pg[1e+20]",
            "tests/integration/test_structured_factor_persistence.py::test_structured_raw_float_commitment_actual_pg[5e-324]",
            "tests/integration/test_structured_factor_persistence.py::test_structured_raw_float_commitment_actual_pg[-5e-324]",
            'tests/integration/test_structured_factor_persistence.py::test_structured_raw_unsafe_actual_pg[{"value":NaN}]',
            'tests/integration/test_structured_factor_persistence.py::test_structured_raw_unsafe_actual_pg[{"value":Infinity}]',
            'tests/integration/test_structured_factor_persistence.py::test_structured_raw_unsafe_actual_pg[{"value":1e9999}]',
            'tests/integration/test_structured_factor_persistence.py::test_structured_raw_unsafe_actual_pg[{"value":1e-9999}]',
            'tests/integration/test_structured_factor_persistence.py::test_structured_raw_unsafe_actual_pg[{"value":0.0,"value":1.0}]',
            'tests/integration/test_structured_factor_persistence.py::test_structured_raw_unsafe_actual_pg[{ "value":0.0}]',
            "tests/integration/test_structured_factor_persistence.py::test_structured_raw_large_original_bytes_not_old_one_mib_codec",
            "tests/integration/test_structured_factor_persistence.py::test_structured_version_immutable_and_not_legacy_strategy_actual_pg",
            "tests/integration/test_direct_analysis_persistence.py::test_declared_plan_precedes_generation_and_has_no_fabricated_result",
            "tests/integration/test_direct_analysis_persistence.py::test_declared_plan_is_immutable_in_actual_pg[objective_sha256-cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc]",
            "tests/integration/test_direct_analysis_persistence.py::test_declared_plan_is_immutable_in_actual_pg[qlib_installed_parent_image_id-sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc]",
            "tests/integration/test_direct_analysis_persistence.py::test_declared_plan_is_immutable_in_actual_pg[source_policy_sha256-cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc]",
            "tests/integration/test_direct_analysis_persistence.py::test_downgrade_rejects_even_preexecution_analysis_plan",
            "tests/integration/test_direct_analysis_persistence.py::test_022_installs_frozen_semantic_and_immutable_triggers",
            "tests/integration/test_direct_analysis_persistence.py::test_actual_pg_lossless_float_commitments[0.0]",
            "tests/integration/test_direct_analysis_persistence.py::test_actual_pg_lossless_float_commitments[-0.0]",
            "tests/integration/test_direct_analysis_persistence.py::test_actual_pg_lossless_float_commitments[1.0]",
            "tests/integration/test_direct_analysis_persistence.py::test_actual_pg_lossless_float_commitments[-1.0]",
            "tests/integration/test_direct_analysis_persistence.py::test_actual_pg_lossless_float_commitments[1e-07]",
            "tests/integration/test_direct_analysis_persistence.py::test_actual_pg_lossless_float_commitments[1e-20]",
            "tests/integration/test_direct_analysis_persistence.py::test_actual_pg_lossless_float_commitments[5e-324]",
            "tests/integration/test_direct_analysis_persistence.py::test_actual_pg_lossless_float_commitments[-5e-324]",
            'tests/integration/test_direct_analysis_persistence.py::test_actual_pg_raw_commitment_rejects_unsafe_or_noncanonical[{"value":NaN}]',
            'tests/integration/test_direct_analysis_persistence.py::test_actual_pg_raw_commitment_rejects_unsafe_or_noncanonical[{"value":Infinity}]',
            'tests/integration/test_direct_analysis_persistence.py::test_actual_pg_raw_commitment_rejects_unsafe_or_noncanonical[{"value":1e999}]',
            'tests/integration/test_direct_analysis_persistence.py::test_actual_pg_raw_commitment_rejects_unsafe_or_noncanonical[{"value":1e-999}]',
            'tests/integration/test_direct_analysis_persistence.py::test_actual_pg_raw_commitment_rejects_unsafe_or_noncanonical[{"value":0.0,"value":1.0}]',
            'tests/integration/test_direct_analysis_persistence.py::test_actual_pg_raw_commitment_rejects_unsafe_or_noncanonical[{ "value":0.0}]',
            "tests/integration/test_rd_controller_persistence.py::test_old_precursor_cannot_receive_protected_child",
            "tests/integration/test_rd_controller_persistence.py::test_unexecuted_precursor_cannot_record_adoption",
            "tests/integration/test_execution_snapshot_persistence.py::test_snapshot_cannot_steal_existing_object_reservation[READ COMMITTED]",
            "tests/integration/test_execution_snapshot_persistence.py::test_snapshot_cannot_steal_existing_object_reservation[REPEATABLE READ]",
            "tests/integration/test_execution_snapshot_persistence.py::test_snapshot_cannot_steal_existing_object_reservation[SERIALIZABLE]",
            "tests/integration/test_execution_snapshot_persistence.py::test_snapshot_installs_immediate_claim_and_deferred_full_chain",
            "tests/integration/test_execution_snapshot_persistence.py::test_unbound_execution_object_is_not_accepted",
            "tests/integration/test_engine_history_reads.py::test_rd_history_actual_owner_list_detail_source_and_reload",
            "tests/integration/test_engine_history_reads.py::test_engine_public_entry_remains_closed_and_legacy_detail_is_not_reinterpreted",
            "tests/integration/test_rd_source_persistence.py::test_actual_files_fenced_publisher_and_host_parser_witness",
            "tests/integration/test_rd_source_persistence.py::test_actual_continuation_sources_are_reparsed_in_order",
            "tests/integration/test_rd_source_persistence.py::test_nonempty_invalid_python_is_retained_without_execution",
            "tests/integration/test_rd_source_persistence.py::test_exact_source_publication_replay_and_wrong_bytes",
            "tests/integration/test_rd_source_persistence.py::test_substituted_fence_cannot_publish_retained_source[owner_id]",
            "tests/integration/test_rd_source_persistence.py::test_substituted_fence_cannot_publish_retained_source[run_id]",
            "tests/integration/test_rd_source_persistence.py::test_substituted_fence_cannot_publish_retained_source[attempt_id]",
            "tests/integration/test_rd_source_persistence.py::test_substituted_fence_cannot_publish_retained_source[lease_token]",
            "tests/integration/test_rd_source_persistence.py::test_substituted_fence_cannot_publish_retained_source[generation]",
            "tests/integration/test_rd_source_persistence.py::test_direct_sql_updates_cannot_rewrite_evidence[factor_generation_sources-source_sha256-0000000000000000000000000000000000000000000000000000000000000000]",
            "tests/integration/test_rd_source_persistence.py::test_direct_sql_updates_cannot_rewrite_evidence[factor_source_read_witnesses-raw_text_sha256-0000000000000000000000000000000000000000000000000000000000000000]",
            "tests/integration/test_rd_source_persistence.py::test_direct_sql_updates_cannot_rewrite_evidence[factor_source_read_witnesses-selected_first_ordinal-1]",
            "tests/integration/test_rd_source_persistence.py::test_direct_sql_updates_cannot_rewrite_evidence[factor_generation_model_operations-relation-continuation]",
            "tests/integration/test_rd_source_persistence.py::test_direct_sql_updates_cannot_rewrite_evidence[research_model_operations-protocol-mineagent.neutral-model.v1]",
            "tests/integration/test_rd_source_persistence.py::test_direct_sql_updates_cannot_rewrite_evidence[research_budget_reservations-logical_key-forged-reservation]",
            "tests/integration/test_rd_source_persistence.py::test_direct_sql_new_wrong_protocol_request_rejected",
            "tests/integration/test_rd_source_persistence.py::test_tampered_real_response_bytes_cannot_revalidate_witness",
            "tests/integration/test_rd_source_persistence.py::test_direct_sql_new_member_serializes_against_source_finalization",
            "tests/integration/test_rd_source_persistence.py::test_unicode_source_retains_exact_utf8_bytes",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[None-True]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[None-False]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[True-True]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[True-False]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[False-True]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[False-False]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[0-True]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[0-False]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[-1-True]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[-1-False]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[9007199254740991-True]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[9007199254740991-False]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[-9007199254740991-True]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[-9007199254740991-False]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[-True]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[-False]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[plain-True]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[plain-False]",
            'tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256["\\\\/-True]',
            'tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256["\\\\/-False]',
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[\\x08\\t\\n\\x0c\\r-True]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[\\x08\\t\\n\\x0c\\r-False]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[\\x01\\x02\\x03\\x04\\x05\\x06\\x07\\x08\\t\\n\\x0b\\x0c\\r\\x0e\\x0f\\x10\\x11\\x12\\x13\\x14\\x15\\x16\\x17\\x18\\x19\\x1a\\x1b\\x1c\\x1d\\x1e\\x1f-True]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[\\x01\\x02\\x03\\x04\\x05\\x06\\x07\\x08\\t\\n\\x0b\\x0c\\r\\x0e\\x0f\\x10\\x11\\x12\\x13\\x14\\x15\\x16\\x17\\x18\\x19\\x1a\\x1b\\x1c\\x1d\\x1e\\x1f-False]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[\\x7f\\x80\\xe9\\u4e2d\\u6587\\u2028\\u2029\\uffff-True]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[\\x7f\\x80\\xe9\\u4e2d\\u6587\\u2028\\u2029\\uffff-False]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[\\U0001f600\\U00010000\\U0010ffff-True]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[\\U0001f600\\U00010000\\U0010ffff-False]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[value14-True]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[value14-False]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[value15-True]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[value15-False]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[value16-True]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[value16-False]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[value17-True]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[value17-False]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[value18-True]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_python_canonical_bytes_and_sha256[value18-False]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_fractional_or_float_tokens_fail_closed[1.0]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_fractional_or_float_tokens_fail_closed[1.5]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_fractional_or_float_tokens_fail_closed[-0.0]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_fractional_or_float_tokens_fail_closed[-1.5]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_fractional_or_float_tokens_fail_closed[value4]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_fractional_or_float_tokens_fail_closed[value5]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_invalid_caller_depth_rejected[-1]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_invalid_caller_depth_rejected[25]",
            "tests/integration/test_rdagent_canonical_postgres.py::test_nested_depth_boundary",
            "tests/integration/test_rdagent_canonical_postgres.py::test_exact_output_byte_boundary",
            "tests/integration/test_rdagent_canonical_postgres.py::test_ascii_escape_expansion_counts_output_not_input",
            "tests/integration/test_rdagent_canonical_postgres.py::test_sql_null_strictness_distinct_from_json_null",
            "tests/integration/test_rdagent_canonical_postgres.py::test_jsonb_lexical_loss_is_explicit_not_raw_validation",
            "tests/integration/test_rdagent_identity_postgres.py::test_valid_full_normalized_config",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_negative_differential[path0-True]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_negative_differential[path1-1.0]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_negative_differential[path2-131072.0]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_negative_differential[path3-True]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_negative_differential[path4-0]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_negative_differential[path5-9]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_negative_differential[path6-2]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_negative_differential[path7-1023]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_negative_differential[path8-0]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_negative_differential[path9-9007199254740992]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_negative_differential[path10-False]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_negative_differential[path11-2]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_negative_differential[path12-64]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_negative_differential[path13--1]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_negative_differential[path14-Definitely/Not_A_Zone]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_negative_differential[path15-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_negative_differential[path16-invalid]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_negative_differential[path17-00000000000000000000000000000002]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_negative_differential[path18-2024-02-30]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_negative_differential[path19-9999-01-01]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_negative_differential[path20-replacement20]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_negative_differential[path21-replacement21]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_negative_differential[path22-2024-01-01T00:00:00]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_negative_differential[path23-2024-01-01T00:00:00+00:00]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_negative_differential[path24-]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_negative_differential[path25-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_negative_differential[path26-bad\\tname]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_negative_differential[path27-mineagent.factor-prefix-candidate.v1]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_negative_differential[path28-sha256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA]",
            "tests/integration/test_rdagent_identity_postgres.py::test_request_differential[mineagent.rdagent-chat.v1-value0-True]",
            "tests/integration/test_rdagent_identity_postgres.py::test_request_differential[mineagent.rdagent-embedding.v1-value1-True]",
            "tests/integration/test_rdagent_identity_postgres.py::test_request_differential[mineagent.rdagent-chat.v1-value2-False]",
            "tests/integration/test_rdagent_identity_postgres.py::test_request_differential[mineagent.rdagent-chat.v1-value3-False]",
            "tests/integration/test_rdagent_identity_postgres.py::test_request_differential[mineagent.rdagent-chat.v1-value4-False]",
            "tests/integration/test_rdagent_identity_postgres.py::test_request_differential[mineagent.rdagent-chat.v1-value5-False]",
            "tests/integration/test_rdagent_identity_postgres.py::test_request_differential[mineagent.rdagent-chat.v1-value6-False]",
            "tests/integration/test_rdagent_identity_postgres.py::test_request_differential[mineagent.rdagent-chat.v1-value7-False]",
            "tests/integration/test_rdagent_identity_postgres.py::test_request_differential[mineagent.rdagent-chat.v1-value8-False]",
            "tests/integration/test_rdagent_identity_postgres.py::test_request_differential[mineagent.rdagent-embedding.v1-value9-False]",
            "tests/integration/test_rdagent_identity_postgres.py::test_request_differential[mineagent.rdagent-embedding.v1-value10-False]",
            "tests/integration/test_rdagent_identity_postgres.py::test_request_differential[mineagent.rdagent-embedding.v1-value11-False]",
            "tests/integration/test_rdagent_identity_postgres.py::test_request_differential[mineagent.rdagent-embedding.v1-value12-False]",
            "tests/integration/test_rdagent_identity_postgres.py::test_request_differential[mineagent.rdagent-embedding.v1-value13-False]",
            "tests/integration/test_rdagent_identity_postgres.py::test_context_exact_service_shape[logical_step-0-True]",
            "tests/integration/test_rdagent_identity_postgres.py::test_context_exact_service_shape[logical_step-7-True]",
            "tests/integration/test_rdagent_identity_postgres.py::test_context_exact_service_shape[logical_step-8-False]",
            "tests/integration/test_rdagent_identity_postgres.py::test_context_exact_service_shape[logical_step--1-False]",
            "tests/integration/test_rdagent_identity_postgres.py::test_context_exact_service_shape[logical_step-True-False]",
            "tests/integration/test_rdagent_identity_postgres.py::test_context_exact_service_shape[logical_step-0.0-False]",
            "tests/integration/test_rdagent_identity_postgres.py::test_context_exact_service_shape[task_index-False-False]",
            "tests/integration/test_rdagent_identity_postgres.py::test_context_exact_service_shape[task_index-1-False]",
            "tests/integration/test_rdagent_identity_postgres.py::test_context_exact_service_shape[task_index-0-False]",
            "tests/integration/test_rdagent_identity_postgres.py::test_context_exact_service_shape[spec_sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA-False]",
            "tests/integration/test_rdagent_identity_postgres.py::test_context_exact_service_shape[input_sha256-None-False]",
            "tests/integration/test_rdagent_identity_postgres.py::test_context_exact_service_shape[schema_version-unknown-False]",
            "tests/integration/test_rdagent_identity_postgres.py::test_context_root_and_exact_keys[missing]",
            "tests/integration/test_rdagent_identity_postgres.py::test_context_root_and_exact_keys[extra]",
            "tests/integration/test_rdagent_identity_postgres.py::test_context_root_and_exact_keys[array]",
            "tests/integration/test_rdagent_identity_postgres.py::test_context_root_and_exact_keys[null]",
            "tests/integration/test_rdagent_identity_postgres.py::test_context_root_and_exact_keys[boolean]",
            "tests/integration/test_rdagent_identity_postgres.py::test_json_byte_bound_is_python_canonical_length[value0-True]",
            "tests/integration/test_rdagent_identity_postgres.py::test_json_byte_bound_is_python_canonical_length[value0-False]",
            "tests/integration/test_rdagent_identity_postgres.py::test_json_byte_bound_is_python_canonical_length[value1-True]",
            "tests/integration/test_rdagent_identity_postgres.py::test_json_byte_bound_is_python_canonical_length[value1-False]",
            "tests/integration/test_rdagent_identity_postgres.py::test_json_byte_bound_is_python_canonical_length[value2-True]",
            "tests/integration/test_rdagent_identity_postgres.py::test_json_byte_bound_is_python_canonical_length[value2-False]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_nested_semantics[missing_default]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_nested_semantics[extra_nested]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_nested_semantics[fitted_ready]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_nested_semantics[fitted_backdated]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_nested_semantics[locks_reversed]",
            "tests/integration/test_rdagent_identity_postgres.py::test_config_nested_semantics[price_drift]",
            "tests/integration/test_rdagent_source_backup_postgres.py::test_real_rd_source_snapshot_reads_and_reparses_original_files",
            "tests/integration/test_rdagent_source_backup_postgres.py::test_real_rd_snapshot_metadata_drift_rejected[witness]",
            "tests/integration/test_rdagent_source_backup_postgres.py::test_real_rd_snapshot_metadata_drift_rejected[source_owner]",
            "tests/integration/test_rdagent_source_backup_postgres.py::test_real_rd_snapshot_metadata_drift_rejected[consumer]",
            "tests/integration/test_rdagent_source_backup_postgres.py::test_real_rd_snapshot_metadata_drift_rejected[response_hash]",
            "tests/integration/test_rdagent_source_backup_postgres.py::test_real_rd_snapshot_row_and_byte_caps",
            "tests/integration/test_native_storage_persistence.py::test_native_claim_cannot_steal_committed_research_object_reservation[READ COMMITTED]",
            "tests/integration/test_native_storage_persistence.py::test_native_claim_cannot_steal_committed_research_object_reservation[REPEATABLE READ]",
            "tests/integration/test_native_storage_persistence.py::test_native_claim_cannot_steal_committed_research_object_reservation[SERIALIZABLE]",
            "tests/integration/test_native_storage_persistence.py::test_native_object_without_exact_publication_outcome_is_rejected",
            "tests/integration/test_native_storage_persistence.py::test_native_schema_uses_immediate_claim_and_deferred_closure",
            "tests/integration/test_native_storage_persistence.py::test_native_outcome_retains_full_charge_trigger_on_reservation",
            "tests/integration/test_native_storage_persistence.py::test_native_sql_commitment_matches_bounded_unicode_integer_python_codec[value0]",
            "tests/integration/test_native_storage_persistence.py::test_native_sql_commitment_matches_bounded_unicode_integer_python_codec[value1]",
            "tests/integration/test_native_storage_persistence.py::test_native_sql_commitment_refuses_float_codec_ambiguity",
            "tests/integration/test_combined_publication_scope.py::test_each_legacy_or_neutral_lock_blocks_combined_admission_without_partial_locks[0]",
            "tests/integration/test_combined_publication_scope.py::test_each_legacy_or_neutral_lock_blocks_combined_admission_without_partial_locks[1]",
            "tests/integration/test_combined_publication_scope.py::test_each_legacy_or_neutral_lock_blocks_combined_admission_without_partial_locks[2]",
            "tests/integration/test_combined_publication_scope.py::test_each_legacy_or_neutral_lock_blocks_combined_admission_without_partial_locks[3]",
            "tests/integration/test_combined_publication_scope.py::test_each_legacy_or_neutral_lock_blocks_combined_admission_without_partial_locks[4]",
            "tests/integration/test_combined_publication_scope.py::test_combined_transaction_holds_all_locks_and_only_object_capability",
            "tests/integration/test_combined_publication_scope.py::test_plain_objects_cannot_gain_factor_publication_authority",
            "tests/integration/test_combined_publication_scope.py::test_factor_numeric_explicitly_supplies_both_without_widening_old_scope",
            "tests/integration/test_numeric_process_persistence.py::test_real_numeric_obligation_has_no_ticket_launch_stop_or_output",
            "tests/integration/test_numeric_process_persistence.py::test_real_numeric_evidence_prevents_downgrade",
            "tests/integration/test_numeric_process_persistence.py::test_real_numeric_immutable_rows_reject_mutation[ticket]",
            "tests/integration/test_numeric_process_persistence.py::test_real_numeric_immutable_rows_reject_mutation[policy]",
            "tests/integration/test_numeric_process_persistence.py::test_real_numeric_immutable_rows_reject_mutation[profile]",
            "tests/integration/test_numeric_process_persistence.py::test_real_numeric_immutable_rows_reject_mutation[role]",
            "tests/integration/test_process_versioning.py::test_real_versioned_migration_requires_exclusive_maintenance[upgrade]",
            "tests/integration/test_process_versioning.py::test_real_versioned_migration_requires_exclusive_maintenance[downgrade]",
            "tests/integration/test_process_versioning.py::test_real_versioned_upgrade_backfills_old_admission_without_commitment_drift",
            "tests/integration/test_process_versioning.py::test_real_versioned_direct_sql_cannot_bypass_closed_profile[missing_version]",
            "tests/integration/test_process_versioning.py::test_real_versioned_direct_sql_cannot_bypass_closed_profile[missing_policy]",
            "tests/integration/test_process_versioning.py::test_real_versioned_direct_sql_cannot_bypass_closed_profile[v1_noipc]",
            "tests/integration/test_process_versioning.py::test_real_versioned_direct_sql_cannot_bypass_closed_profile[v2_candidate]",
            "tests/integration/test_process_versioning.py::test_real_versioned_direct_sql_cannot_bypass_closed_profile[v2_controller]",
            "tests/integration/test_process_versioning.py::test_real_versioned_direct_sql_cannot_bypass_closed_profile[unknown_version]",
            "tests/integration/test_process_versioning.py::test_real_versioned_direct_sql_cannot_bypass_closed_profile[null_ticket]",
            "tests/integration/test_process_versioning.py::test_real_versioned_columns_are_immutable",
            "tests/integration/test_process_versioning.py::test_real_versioned_migration_refuses_concurrent_readers[upgrade]",
            "tests/integration/test_process_versioning.py::test_real_versioned_migration_refuses_concurrent_readers[downgrade]",
            "tests/integration/test_neutral_lifecycle.py::test_neutral_nested_transaction_never_erases_outer_work",
            "tests/integration/test_neutral_lifecycle.py::test_neutral_helper_cannot_commit_or_reuse_released_authority",
            "tests/integration/test_neutral_lifecycle.py::test_neutral_real_scope_substitution_is_rejected[owner_id]",
            "tests/integration/test_neutral_lifecycle.py::test_neutral_real_scope_substitution_is_rejected[run_id]",
            "tests/integration/test_neutral_lifecycle.py::test_neutral_real_scope_substitution_is_rejected[attempt_id]",
            "tests/integration/test_neutral_lifecycle.py::test_neutral_real_scope_substitution_is_rejected[lease_token]",
            "tests/integration/test_neutral_lifecycle.py::test_neutral_real_scope_substitution_is_rejected[generation]",
            "tests/integration/test_neutral_lifecycle.py::test_neutral_resource_scope_is_not_implicitly_promoted",
            "tests/integration/test_neutral_lifecycle.py::test_neutral_database_loss_does_not_reconnect",
            "tests/integration/test_neutral_migration.py::test_neutral_zero_argument_immutability_trigger_rejects_changed_values",
            "tests/integration/test_neutral_migration.py::test_neutral_downgrade_prelocks_every_parent_without_wait[users]",
            "tests/integration/test_neutral_migration.py::test_neutral_downgrade_prelocks_every_parent_without_wait[research_runs]",
            "tests/integration/test_neutral_migration.py::test_neutral_downgrade_prelocks_every_parent_without_wait[execution_attempts]",
            "tests/integration/test_neutral_migration.py::test_neutral_downgrade_prelocks_every_parent_without_wait[research_budget_controls]",
            "tests/integration/test_neutral_migration.py::test_neutral_downgrade_rejects_stale_snapshot_and_preserves_new_evidence",
            "tests/integration/test_neutral_objects.py::test_neutral_real_equal_bytes_different_kinds_are_separate_and_charged",
            "tests/integration/test_neutral_objects.py::test_neutral_real_exact_republication_reuses_same_kind_object",
            "tests/integration/test_neutral_objects.py::test_neutral_real_object_identity_is_immutable[owner_id]",
            "tests/integration/test_neutral_objects.py::test_neutral_real_object_identity_is_immutable[object_kind]",
            "tests/integration/test_neutral_objects.py::test_neutral_real_object_identity_is_immutable[sha256]",
            "tests/integration/test_neutral_objects.py::test_neutral_real_object_identity_is_immutable[storage_key]",
            "tests/integration/test_neutral_objects.py::test_neutral_real_publication_budget_cannot_substitute_scope",
            "tests/integration/test_neutral_objects.py::test_neutral_real_pending_peak_reservations_count_toward_physical_quota",
            "tests/integration/test_neutral_objects.py::test_neutral_real_database_loss_after_fsync_preserves_charged_orphan",
            "tests/integration/test_neutral_objects.py::test_neutral_real_lease_loss_after_file_write_leaves_charged_evidence",
            "tests/integration/test_neutral_objects.py::test_neutral_real_pending_object_writer_blocks_terminal_transition[run_terminal]",
            "tests/integration/test_neutral_objects.py::test_neutral_real_pending_object_writer_blocks_terminal_transition[attempt_terminal]",
            "tests/integration/test_neutral_objects.py::test_neutral_real_pending_object_writer_blocks_terminal_transition[generation]",
            "tests/integration/test_neutral_objects.py::test_neutral_real_crash_temporary_blocks_every_retry_without_growth",
            "tests/integration/test_neutral_objects.py::test_neutral_real_success_retains_peak_charge_and_allows_contained_terminal",
            "tests/integration/test_research_process_recovery.py::test_process_admission_rejects_wrong_scope[owner_id]",
            "tests/integration/test_research_process_recovery.py::test_process_admission_rejects_wrong_scope[run_id]",
            "tests/integration/test_research_process_recovery.py::test_process_admission_rejects_wrong_scope[attempt_id]",
            "tests/integration/test_research_process_recovery.py::test_process_admission_rejects_wrong_scope[generation]",
            "tests/integration/test_research_process_recovery.py::test_process_unknown_stop_blocks_terminal_and_keeps_reservation",
            "tests/integration/test_research_process_recovery.py::test_process_cannot_reuse_launch_or_wrong_reservation",
            "tests/integration/test_research_process_recovery.py::test_real_process_stop_after_lease_loss_is_containment_only",
            "tests/integration/test_research_process_recovery.py::test_process_insert_enforces_composite_scope[owner_id]",
            "tests/integration/test_research_process_recovery.py::test_process_insert_enforces_composite_scope[run_id]",
            "tests/integration/test_research_process_recovery.py::test_process_insert_enforces_composite_scope[attempt_id]",
            "tests/integration/test_research_process_recovery.py::test_process_insert_enforces_composite_scope[generation]",
            "tests/integration/test_research_process_recovery.py::test_process_database_terminal_and_generation_fail_closed",
            "tests/integration/test_research_process_recovery.py::test_process_profile_requires_every_reserved_peak[process_slots]",
            "tests/integration/test_research_process_recovery.py::test_process_profile_requires_every_reserved_peak[wall_milliseconds]",
            "tests/integration/test_research_process_recovery.py::test_process_profile_requires_every_reserved_peak[storage_bytes]",
            "tests/integration/test_research_process_recovery.py::test_process_launch_requires_committed_current_one_use_authority[absent]",
            "tests/integration/test_research_process_recovery.py::test_process_launch_requires_committed_current_one_use_authority[rolled_back]",
            "tests/integration/test_research_process_recovery.py::test_process_launch_requires_committed_current_one_use_authority[stale]",
            "tests/integration/test_research_process_recovery.py::test_process_launch_requires_committed_current_one_use_authority[consumed]",
            "tests/integration/test_model_broker_accounting.py::test_neutral_identical_physical_failures_charge_separately_and_completion_replays",
            "tests/integration/test_model_broker_accounting.py::test_neutral_request_drift_cannot_create_another_logical_operation",
            "tests/integration/test_model_broker_accounting.py::test_neutral_lost_submission_remains_reserved_and_cannot_retry",
            "tests/integration/test_model_broker_accounting.py::test_neutral_concurrent_reservations_cannot_overspend_run_cap",
            "tests/integration/test_model_broker_accounting.py::test_neutral_budget_survives_generation_change_without_reset",
            "tests/integration/test_model_broker_accounting.py::test_neutral_nonmodel_refund_requires_domain_stop_proof[process_unit]",
            "tests/integration/test_model_broker_accounting.py::test_neutral_nonmodel_refund_requires_domain_stop_proof[object_storage]",
            "tests/integration/test_model_broker_accounting.py::test_neutral_cancellation_interrupts_real_retry_sleep",
            "tests/integration/test_model_broker_accounting.py::test_neutral_direct_sql_cannot_rewrite_ledger[caps]",
            "tests/integration/test_model_broker_accounting.py::test_neutral_direct_sql_cannot_rewrite_ledger[reservation_amount]",
            "tests/integration/test_model_broker_accounting.py::test_neutral_direct_sql_cannot_rewrite_ledger[delete_reservation]",
            "tests/integration/test_model_broker_accounting.py::test_neutral_direct_sql_cannot_rewrite_ledger[projection]",
            "tests/integration/test_model_broker_accounting.py::test_neutral_direct_sql_cannot_forge_completed_response_tuple",
            "tests/integration/test_model_broker_accounting.py::test_neutral_direct_sql_purpose_cannot_relabel_chat_as_embedding",
            "tests/integration/test_model_broker_accounting.py::test_neutral_context_abort_persists_and_blocks_all_new_work",
            "tests/integration/test_model_broker_accounting.py::test_neutral_empty_context_abort_prevents_later_first_operation",
            "tests/integration/test_model_broker_accounting.py::test_neutral_direct_sql_cannot_reverse_context_abort[reason]",
            "tests/integration/test_model_broker_accounting.py::test_neutral_direct_sql_cannot_reverse_context_abort[new_operation]",
            "tests/integration/test_model_broker_accounting.py::test_neutral_direct_sql_cannot_reverse_context_abort[delete]",
        ),
        "isolation": (
            "tests/integration/test_research_supervisor_isolation.py::test_real_whole_unit_child_survives_controller_then_verified_stop",
            "tests/integration/test_research_supervisor_isolation.py::test_real_peer_authentication_rejects_outside_pid_and_wrong_ticket",
            "tests/integration/test_research_supervisor_isolation.py::test_real_uncertain_create_is_recovered_by_exact_journal[create]",
            "tests/integration/test_research_supervisor_isolation.py::test_real_uncertain_create_is_recovered_by_exact_journal[start]",
            "tests/integration/test_research_supervisor_isolation.py::test_real_tampered_journal_never_authorizes_stop",
            "tests/integration/test_research_supervisor_isolation.py::test_real_host_deadline_stops_still_running_fixed_fixture",
            "tests/integration/test_research_supervisor_isolation.py::test_real_pid1_deadline_survives_host_caller_death",
            "tests/integration/test_research_supervisor_isolation.py::test_real_live_peer_cannot_submit_after_process_projection_uncertain",
            "tests/integration/test_research_supervisor_isolation.py::test_real_recovery_manifest_rejects_unsafe_projection_drift",
        ),
        "restore": (
            "tests/integration/test_neutral_restore.py::test_neutral_cold_restore_preserves_rows_bytes_and_uncertain_accounting[018_rd_source_persistence]",
            "tests/integration/test_neutral_restore.py::test_neutral_cold_restore_preserves_rows_bytes_and_uncertain_accounting[019_native_hdf_storage]",
            "tests/integration/test_neutral_restore.py::test_neutral_cold_restore_preserves_rows_bytes_and_uncertain_accounting[020_rd_controller]",
            "tests/integration/test_neutral_restore.py::test_neutral_cold_restore_preserves_rows_bytes_and_uncertain_accounting[021_execution_snapshots]",
            "tests/integration/test_neutral_restore.py::test_neutral_cold_restore_preserves_rows_bytes_and_uncertain_accounting[017_numeric_process_boundary]",
            "tests/integration/test_neutral_restore.py::test_neutral_cold_restore_preserves_rows_bytes_and_uncertain_accounting[015_neutral_primitives]",
            "tests/integration/test_neutral_restore.py::test_neutral_cold_restore_preserves_rows_bytes_and_uncertain_accounting[016_process_versioning]",
            "tests/integration/test_neutral_restore.py::test_historical_upgrade_preserves_legacy_pins_and_allows_neutral_publication[011_note_pin_trigger]",
            "tests/integration/test_neutral_restore.py::test_historical_upgrade_preserves_legacy_pins_and_allows_neutral_publication[013_factor_import]",
        ),
    }
)

REQUIRED_SOURCES = MappingProxyType(
    {
        "postgres": frozenset(
            {
                "tests/integration/test_structured_factor_persistence.py",
                "tests/integration/test_direct_analysis_persistence.py",
                "tests/integration/test_execution_snapshot_persistence.py",
                "tests/integration/test_rd_controller_persistence.py",
                "tests/integration/test_engine_history_reads.py",
                "tests/integration/test_combined_publication_scope.py",
                "tests/integration/test_native_storage_persistence.py",
                "tests/integration/test_rd_source_persistence.py",
                "tests/integration/test_rdagent_canonical_postgres.py",
                "tests/integration/test_rdagent_identity_postgres.py",
                "tests/integration/test_rdagent_source_backup_postgres.py",
                "tests/integration/test_neutral_lifecycle.py",
                "tests/integration/test_process_versioning.py",
                "tests/integration/test_numeric_process_persistence.py",
                "tests/integration/test_neutral_migration.py",
                "tests/integration/test_research_process_recovery.py",
                "tests/integration/test_model_broker_accounting.py",
                "tests/integration/test_neutral_objects.py",
            }
        ),
        "isolation": frozenset(
            {"tests/integration/test_research_supervisor_isolation.py"}
        ),
        "restore": frozenset({"tests/integration/test_neutral_restore.py"}),
    }
)


def required_cases(profile):
    nodes = REQUIRED.get(profile)
    if not nodes or len(nodes) != len(set(nodes)):
        raise ValueError("Closed nonempty neutral case inventory required")
    if {node.split("::")[0] for node in nodes} != REQUIRED_SOURCES[profile]:
        raise ValueError("Every neutral acceptance boundary must be represented")
    identities = []
    for node in nodes:
        path, name = node.split("::")
        if not path.startswith("tests/") or not path.endswith(".py"):
            raise ValueError("Unsupported neutral test source")
        identities.append((path.removesuffix(".py").replace("/", "."), name))
    return tuple(identities)


def report_root(path):
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > MAX_REPORT_BYTES
    ):
        raise ValueError("Fresh bounded neutral report required")
    raw = path.read_bytes()
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise ValueError("Unsupported report framing")
    return ET.fromstring(raw)


def verify_report(path, profile, exit_code):
    expected = required_cases(profile)
    root = report_root(path)
    suites = list(root)
    cases = list(root.iter("testcase"))
    if (
        exit_code != 0
        or root.tag != "testsuites"
        or len(suites) != 1
        or suites[0].tag != "testsuite"
        or Counter((case.get("classname"), case.get("name")) for case in cases)
        != Counter(expected)
        or len(suites[0].findall("testcase")) != len(expected)
        or any(
            next(root.iter(tag), None) is not None
            for tag in ("skipped", "failure", "error")
        )
        or any(
            suites[0].get(key) != value
            for key, value in {
                "tests": str(len(expected)),
                "failures": "0",
                "errors": "0",
                "skipped": "0",
            }.items()
        )
    ):
        raise ValueError("Exact real neutral cases must all execute and pass")


SAFE_EXCEPTION_CLASSES = frozenset(
    {
        "AssertionError",
        "IntegrityError",
        "OperationalError",
        "ProgrammingError",
        "DataError",
        "DBAPIError",
        "DomainError",
        "ProcessError",
        "BrokerAbort",
        "ValueError",
        "TypeError",
        "KeyError",
        "AttributeError",
        "ImportError",
        "TimeoutError",
        "FileNotFoundError",
        "PermissionError",
        "MaintenanceBusy",
        "CheckViolation",
        "ForeignKeyViolation",
        "NotNullViolation",
        "UniqueViolation",
        "UndefinedTable",
        "UndefinedColumn",
        "RaiseException",
    }
)


def failure_outcomes(path, profile):
    """Known identifiers/locations/classes only; never raw SQL, text or values."""
    allowed = set(required_cases(profile))
    root = Path(__file__).resolve().parents[1]
    sources = {}
    for directory in ("backend", "packages", "runtimes", "workers", "tests"):
        for source in (root / directory).rglob("*.py"):
            if (
                source.is_symlink()
                or not source.is_file()
                or source.stat().st_size > 1_000_000
            ):
                continue
            sources[source.relative_to(root).as_posix()] = len(
                source.read_bytes().splitlines()
            )
    result = []
    for case in report_root(path).iter("testcase"):
        identity = (case.get("classname"), case.get("name"))
        if identity not in allowed:
            continue
        for outcome in ("failure", "error", "skipped"):
            failure = case.find(outcome)
            if failure is None:
                continue
            text = failure.text or ""
            frames = set()
            for match in re.finditer(
                r"((?:backend|packages|runtimes|workers|tests)/[A-Za-z0-9_/.-]+\.py):([0-9]{1,6})(?=\D|$)",
                text,
            ):
                filename, line = match[1], int(match[2])
                if filename in sources and 1 <= line <= sources[filename]:
                    frames.add((filename, line))
            classes = sorted(
                name
                for name in SAFE_EXCEPTION_CLASSES
                if re.search(r"\b" + name + r"\b", text)
            )
            item = {"source": identity[0], "case": identity[1], "outcome": outcome}
            if frames:
                item["source_frames"] = [
                    {"file": filename, "line": line}
                    for filename, line in sorted(frames)[:12]
                ]
            if classes:
                item["exception_classes"] = classes[:8]
            result.append(item)
    return result[: len(allowed)]


def validate_source(source):
    if (
        source.get("GITHUB_ACTIONS") != "true"
        or source.get("MINEAGENT_REQUIRE_NEUTRAL_ACCEPTANCE") != "1"
        or source.get("MINEAGENT_RESEARCH_ISOLATION_REQUIRED") != "1"
        or source.get("DATA_PROVIDER_MODE") != "fixture"
        or source.get("E2E_LIVE_PROVIDERS") != "0"
        or source.get("RUN_BACKUP_RESTORE_DRILL") != "1"
        or not source.get("TEST_DATABASE_URL")
        or not source.get("DATABASE_URL")
        or re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            source.get("MINEAGENT_RESEARCH_SUPERVISOR_IMAGE", ""),
        )
        is None
    ):
        raise ValueError("Explicit disposable neutral fixture admission required")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=tuple(REQUIRED), required=True)
    args = parser.parse_args()
    profile = args.profile
    root = Path(__file__).resolve().parents[1]
    destination = root / "var/ci-evidence" / f"neutral-{profile}-junit.xml"
    owner = ProcessOwner(time.monotonic() + PROFILE_SECONDS + 30)
    passed = False
    cleaned = False
    private = None
    report = None
    try:
        validate_source(os.environ)
        required_cases(profile)
        if destination.exists() or destination.is_symlink():
            raise ValueError("Stale neutral evidence destination")
        owner.contain()
        private = Path(tempfile.mkdtemp(prefix="mineagent-neutral-"))
        report = private / "report.xml"
        environment = dict(os.environ)
        # No external pytest selection, injection or plugin autoload may turn
        # the exact inventory into a skipped/filtered or forged successful run.
        for key in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTHONPATH", "PYTHONSTARTUP"):
            environment.pop(key, None)
        environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        owner.run(
            [
                sys.executable,
                "-m",
                "pytest",
                *REQUIRED[profile],
                "-q",
                "--maxfail=1",
                "-o",
                "addopts=",
                "--basetemp",
                str(private / "pytest"),
                f"--junitxml={report}",
            ],
            cwd=root,
            env=environment,
            seconds=PROFILE_SECONDS,
        )
        verify_report(report, profile, 0)
        passed = True
    except Exception:  # noqa: BLE001 - private fixture diagnostics never escape
        print("Neutral acceptance failed; inspect private runner evidence")
    finally:
        if owner.started:
            try:
                cleaned = owner.cleanup()
            except Exception:  # noqa: BLE001 - containment uncertainty is failure
                cleaned = False
        if report is not None and report.is_file() and not report.is_symlink():
            try:
                print(
                    "NEUTRAL_OUTCOMES:"
                    + json.dumps(failure_outcomes(report, profile), sort_keys=True)
                )
                summarize(report, destination)
            except Exception:  # noqa: BLE001 - no raw report output
                passed = False
        # Never erase a path while its writer's containment is unconfirmed.
        if cleaned and private is not None:
            try:
                shutil.rmtree(private)
            except OSError:
                passed = False
    if not passed or not cleaned:
        return 1
    print("Exact neutral profile passed with owned subprocess cleanup")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
