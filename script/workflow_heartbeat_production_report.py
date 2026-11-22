"""Private JUnit projection for the branch-only production heartbeat proof."""

import json
from argparse import ArgumentParser
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

ROOT = Path("var/numeric-private")
CASES = {
    "heartbeat": (
        "test_workflow_heartbeat_production",
        "test_actual_workflow_heartbeat_waits_and_times_out_without_renewal",
    ),
    "bridge": (
        "test_workflow_current_head_baseline",
        "test_actual_current032g_workflow_completes_with_readonly_graph_checks",
    ),
    "h_bridge": (
        "test_workflow_032h_composed",
        "test_actual_032h_workflow_completes_with_cold_restore",
    ),
    "restore": (
        "test_workflow_structured_stage_flow",
        "test_actual_final_rd_checkpoint_drives_one_oe_stage_and_exact031_restore",
    ),
}
_H_MILESTONES = ("current_032h_bridge", "current_032h_cold_restore")
H_BRIDGE = {
    "completed = run_bridge(env, source, tmp_path)": "bridge",
    "call_counts = (len(env.rd_calls), len(env.oe_calls))": "current_graph_readback",
    "assert_restore(": "current_cold_restore",
}
_COORDINATION_CLASS = "tests.integration.test_workflow_heartbeat_rpc_coordination"
_COORDINATION_TEST = "test_exact_workflow_rpc_coordinates_with_heartbeat"
_COORDINATION_CASES = {
    "heartbeat-commits-first": "heartbeat_commits_first",
    "rpc-commits-first": "rpc_commits_first",
    "rpc-rolls-back-first": "rpc_rolls_back_first",
}
# Source anchors are exact statements in the unchanged integration helpers.
# Only a known source frame and the last crossed statement determine a phase.
BRIDGE = {
    "wid = create_workflow(env, browser_engine=engine, browser_tmp_path=tmp_path)": "source_browser",
    "with diagnose_first_rd_drain(env.factory, engine, wid):": "rd_drain",
    "checkpoint = session.get(ResearchWorkflowCheckpoint, root.latest_checkpoint_id)": "rd_checkpoint_readback",
    'for path in (f"execution-snapshots/{rd.snapshot_id}", f"analyses/{rd.analysis_id}"):': "rd_result_release",
    "unreleased = env.client.get(": "rd_release_unreleased",
    'env.client.post(route + "/release", headers=env.foreign).status_code == 404': "rd_release_foreign",
    'released = env.client.post(route + "/release", headers=env.headers)': "rd_release_owner",
    "oe_claim = drive_one(env.factory, env.settings, owner_id=owner, workflow_id=wid)": "oe_claim",
    "attached = session.scalars(": "oe_child_readback",
    'paused = command(env.client, env.headers, wid, "pause_after_stage")': "oe_pause",
    'premature = command(env.client, env.headers, wid, "resume")': "oe_premature_resume",
    '== "child_owned_by_engine_worker"': "oe_queue_boundary",
    "drain(env.factory, env.settings)": "oe_drain",
    "with historical_session(env.factory, owner, env.settings) as session:": "oe_terminal",
}
RESTORE = {
    "assert_restore(drill_databases, tmp_path, source, env, completed)": "historical_restore",
    "assert_unavailable_before032(env, completed)": "note_upgrade",
    "assert_notes_restore(": "note_restore",
    'legacy._upgrade(source, "032f_shared_validation_cost")': "keyset_upgrade",
    "assert_upgrade_and_restore(": "keyset_restore",
}
PHASES = frozenset(
    {
        "setup",
        "teardown",
        "heartbeat",
        "before_bridge",
        "bridge",
        "current_graph_readback",
        "rd_waiting_release",
        *BRIDGE.values(),
        *H_BRIDGE.values(),
        *RESTORE.values(),
    }
)
CODES = frozenset(
    {
        "workflow_owner_busy",
        "neutral_busy",
        "provider_outcome_unknown",
        "lease_fence_lost",
        "deadline_exceeded",
        "workflow_oe_claim_changed",
        "workflow_structured_release_required",
        "workflow_input_capacity_exceeded",
        "engine_result_inconsistent",
        "engine_result_release_required",
        "not_found",
    }
)


_DRAIN_PHASES = {"rd_drain", "rd_waiting_release", "rd_checkpoint_readback"}
_RELEASE_PHASES = {"rd_release_unreleased", "rd_release_foreign", "rd_release_owner"}
_OUTCOME_DETAIL_PHASES = _DRAIN_PHASES | _RELEASE_PHASES | {"oe_claim"}
_OE_CLAIM_TRIGGER_LABELS = {
    "Workflow OE immutable predecessor/input/submission graph differs": "predecessor_input_graph",
    "Workflow OE definition differs from actual predecessor": "predecessor_definition",
    "Workflow OE manual input bytes differ": "manual_input_bytes",
    "Workflow OE derived input bytes differ": "derived_input_bytes",
    "Workflow OE text hash differs": "text_hash",
    "Workflow OE exact frozen preflight differs": "frozen_preflight",
    "Workflow OE requires durable original queue and budget": "queue_budget",
    "Workflow OE root graph differs": "root_graph",
    "Workflow OE reverse immutable graph differs": "reverse_graph",
}
_OE_ATTACHMENT_PROBE = frozenset(_OE_CLAIM_TRIGGER_LABELS.values()) - {
    "root_graph",
    "reverse_graph",
}
_OE_REVERSE_FIELDS = {
    "oe_reverse_root": {"active", "sibling", "active_and_sibling", "all_consistent"},
    "oe_reverse_attachment": _OE_ATTACHMENT_PROBE | {"accepted", "unknown"},
}
_OE_CLAIM_FIELDS = {
    "oe_claim_boundary": {"tick_call", "tick_result", "readback_enter"},
    "oe_claim_predicate": frozenset(_OE_CLAIM_TRIGGER_LABELS.values()),
    "oe_claim_state": {
        "blocked",
        "waiting_release",
        "changed",
        "feedback_allocated",
        "paused",
        "control_pending_or_terminal",
        "child_owned_by_engine_worker",
        "child_queued",
        "completed",
        "structured_stage_closed",
        "workflow_waiting_containment",
        "workflow_child_failed_unverified",
        "workflow_child_cancelled_unverified",
    },
    "oe_claim_error": {"domain", "database", "validation", "other"},
    "oe_claim_domain": CODES
    | {
        "run_quota",
        "resolved_input_limit",
        "workflow_graph_inconsistent",
    },
    "oe_claim_sqlstate": {
        "23503",
        "23505",
        "23514",
        "40001",
        "40P01",
        "55P03",
        "57014",
        "P0001",
    },
    "oe_claim_model": {"WorkflowOEClaim", "WorkflowStructuredRunConfig"},
    "oe_claim_site": {"tick", "read", "dispatch", "claim"},
}
_ROLES = (
    "rd_orchestrator",
    "factor_cutoff_candidate",
    "factor_normalizer",
    "native_evaluator",
)
_UNIT_STATES = frozenset(
    {"launch_intended", "uncertain", "running", "stopped", "unknown"}
)
_RPC_REPLY_METHODS = {"submit_chat", "submit_embedding", "finish_evaluation"}
_RPC_DURATION = {"lt_50ms", "lt_250ms", "lt_1s", "lt_5s", "ge_5s"}
_RPC_REMAINING = {
    "late_250ms_or_more",
    "late_lt_250ms",
    "at_deadline",
    "left_lt_250ms",
    "left_250ms_or_more",
}
_RPC_LATE_COMMIT_UNITS = {
    "provider_receipt",
    "provider_publication",
    "provider_broker",
    "outer_auth",
}
_RPC_TIMING_FIELDS = {
    "rpc_late_commit_unit": _RPC_LATE_COMMIT_UNITS,
    "rpc_auth_elapsed": _RPC_DURATION,
    "rpc_handler_elapsed": _RPC_DURATION,
    "rpc_handler_remaining": _RPC_REMAINING,
    "rpc_commit_max_elapsed": _RPC_DURATION,
    "rpc_commit_deadline_cut": {
        "crossed",
        "already_late",
        "not_observed_past_deadline",
    },
    "rpc_send_elapsed": _RPC_DURATION,
    "rpc_send_remaining": _RPC_REMAINING,
}
_DRAIN_FIELDS = {
    **_RPC_TIMING_FIELDS,
    "release_resource": {"snapshot", "analysis"},
    "release_status": {
        "http_200",
        "http_401",
        "http_403",
        "http_404",
        "http_409",
        "http_422",
        "http_429",
        "http_500",
        "http_503",
        "other",
    },
    "sdk_outcome_cut": {"wire_deadline", "wire_error", "output_rejected"},
    "first_lock_scope": {"owner", "resource", "unobserved"},
    "first_holder": {"workflow_heartbeat", "other", "unstable", "unobserved"},
    "first_caller": {
        "recovery",
        "stop_callback",
        "rpc_auth",
        "rpc_dispatch",
        "admission_callback",
        "controller",
        "other",
    },
    "family": frozenset(
        [
            "rpc",
            "process",
            "provider",
            "domain",
            "value",
            "validation",
            "attribute",
            "type",
            "key",
            "timeout",
            "file_missing",
            "permission",
            "os",
            "other",
            "orm",
            "database",
            "unknown",
        ]
    ),
    "code": frozenset(
        CODES
        | {
            "unknown",
            "deadline",
            "neutral_transaction_lost",
            "attempt_fenced",
            "rd_controller_deadline",
            "rd_controller_daemon_identity",
            "rd_controller_peer",
            "rd_controller_incomplete_generation",
            "rd_controller_original_deadline_completion_missing",
            "rd_controller_incomplete_or_failed_reports",
            "rd_controller_daemon_stop_uncertain",
            "rd_provider_containment_unknown",
            "provider_timeout",
            "original_controller_deadline",
            "provider_secret_unavailable",
            "provider_identity_receipt_unconfirmed",
            "provider_stop_receipt_unconfirmed",
            "provider_containment_unconfirmed",
            "provider_input_rejected",
            "provider_admission_unconfirmed",
            "numeric_process_command_deadline",
            "numeric_process_uncertain",
            "analysis_process_deadline",
            "analysis_process_original_deadline",
            "analysis_process_uncertain",
            "analysis_process_output_rejected",
        }
    ),
    "stage": frozenset(
        [
            "unknown",
            "queue",
            "controller",
            "controller_start",
            "controller_accept",
            "controller_finish",
            "rpc_read",
            "rpc_send",
            "peer",
            "observer",
            "provider",
            "evaluation",
            "failure_phase",
            "failure_finalize",
            "member_response",
            "object_publish",
            "object_reserve",
            "object_persist",
            "failure_document",
            "transaction",
        ]
    ),
    "sqlstate": frozenset(
        [
            "none",
            "other",
            "23503",
            "23505",
            "23514",
            "40001",
            "40P01",
            "55P03",
            "57014",
            "P0001",
        ]
    ),
    "run": frozenset(
        [
            "queued",
            "running",
            "succeeded",
            "failed",
            "cancelled",
            "interrupted",
            "paused",
            "cancel_requested",
            "unknown",
            "diagnostic_unavailable",
        ]
    ),
    "rpc_method": frozenset(
        {
            "unknown",
            "check_control",
            "begin_step",
            "begin_exchange",
            "end_exchange",
            "submit_chat",
            "submit_embedding",
            "semantic_retry",
            "publish_source",
            "reject_source",
            "begin_evaluation",
            "execute_or_reuse",
            "finish_evaluation",
            "generation_boundary",
        }
    ),
    "observer_failure": frozenset(
        {
            "deadline",
            "container_state",
            "host_snapshot",
            "host_read",
            "daemon_top",
            "unknown",
        }
    ),
    "neutral_busy_origin": frozenset(
        {
            "gate_expired",
            "gate_expired_controller",
            "gate_expired_local",
            "gate_same_thread_or_closed",
            "gate_closed_while_waiting",
            "postgres_owner",
            "postgres_resource",
        }
    ),
    "receipt_boundary": frozenset(
        {"reserve", "publication_prepare", "publication_finalize", "bind"}
    ),
    **_OE_CLAIM_FIELDS,
    **_OE_REVERSE_FIELDS,
    **dict.fromkeys(_ROLES, _UNIT_STATES),
}

_MASKED_CONTROLLER_FIELDS = {
    "family": _DRAIN_FIELDS["family"],
    "code": _DRAIN_FIELDS["code"]
    | {
        "rd_controller_daemon_identity",
        "rd_controller_peer",
        "rd_controller_not_running",
        "rd_controller_launch",
        "rd_controller_receipts",
        "rd_controller_peer_drift",
        "original_controller_deadline",
        "rd_controller_expired_before_create",
        "rd_controller_expired_before_start",
        "rd_controller_expired_peer",
        "rd_controller_kernel_deadline_exceeded",
        "rd_controller_absence_uncertain",
        "numeric_process_command_cleanup",
        "numeric_process_command_failed",
        "numeric_process_command_output",
        "analysis_process_authority",
        "analysis_process_runtime_identity",
        "analysis_process_kernel_identity",
        "analysis_process_identity_unobserved",
        "analysis_process_created_cleanup_uncertain",
        "analysis_process_journal_identity",
        "analysis_process_root_identity",
        "analysis_process_claim",
        "analysis_process_retained_input_drift",
        "provider_secret_unavailable",
        "provider_identity_receipt_unconfirmed",
        "provider_stop_receipt_unconfirmed",
    },
    "stage": _DRAIN_FIELDS["stage"],
    "sqlstate": _DRAIN_FIELDS["sqlstate"],
    "rpc_method": _DRAIN_FIELDS["rpc_method"],
    "neutral_busy_origin": _DRAIN_FIELDS["neutral_busy_origin"],
    "receipt_boundary": _DRAIN_FIELDS["receipt_boundary"],
    "broker_boundary": frozenset(
        {
            "request_read",
            "reserve",
            "admit",
            "intent",
            "started",
            "stop",
            "response_publish",
            "settle",
        }
    ),
    "validation_boundary": {
        "request",
        "configuration",
        "replay",
        "sdk_intent",
        "sdk_started",
        "sdk_stopped",
        "usage",
        "embedding_response",
        "chat_response",
        "response_validation",
        "settlement",
    },
    "validation_field": {
        "schema_version",
        "kind",
        "model_profile_id",
        "model_binding_sha256",
        "model",
        "messages",
        "role",
        "content",
        "finish_reason",
        "max_output_tokens",
        "temperature_millionths",
        "response_format",
        "inputs",
        "dimensions",
        "admitted_monotonic_ns",
        "deadline_monotonic_ns",
        "maximum_duration_ms",
        "boot_id",
        "identity",
        "observed_monotonic_ns",
        "leader_reaped",
        "process_group_absent",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "provider_request_id",
        "usage_source",
        "billing_status",
        "vectors",
        "usage",
        "transport_id",
        "request_sha256",
        "process_id",
        "process_group_id",
    },
    "validation_type": {
        "value_error",
        "missing",
        "int_type",
        "float_type",
        "bool_type",
        "string_type",
        "uuid_type",
        "uuid_parsing",
        "string_pattern_mismatch",
        "literal_error",
        "finite_number",
        "greater_than",
        "greater_than_equal",
        "less_than_equal",
        "too_short",
        "too_long",
        "string_too_short",
        "string_too_long",
        "tuple_type",
        "extra_forbidden",
        "model_type",
    },
    "analysis_stage": {
        "worker",
        "record_identity",
        "record_containment",
        "execute",
        "verify_launch",
        "verify_runtime",
        "observe_identity",
        "stop",
        "cleanup_unobserved",
        "observe_leader",
        "verify_inspect",
        "read_frame",
        "live_frame",
        "complete_stream",
        "read_journal",
        "read_record",
        "docker_command",
        "docker_inspect",
    },
    "docker_operation": {
        "unknown",
        "image_inspect",
        "container_inspect",
        "create",
        "start",
        "top",
        "remove_force",
    },
}
_DRAIN_FIELDS.update(
    {
        "masked_state": {"observed", "no_prior_failure", "unavailable"},
        "masked_chain": {"single", "unique_leaf", "ambiguous", "overflow"},
        **{
            f"{scope}_{key}": values
            for scope in ("masked", "nested")
            for key, values in _MASKED_CONTROLLER_FIELDS.items()
        },
    }
)


def _masked_controller_observation(failure):
    if failure.get("code") != "rd_provider_containment_unknown":
        return {}
    detail = failure.get("masked_controller")
    if type(detail) is not dict:
        return {}
    state = _emit_value("masked_state", detail.get("state"))
    if state == "unavailable":
        return {"masked_state": state} if detail.get("state") == state else {}
    result = {"masked_state": state}
    if state != "observed":
        return result
    chain = _emit_value("masked_chain", detail.get("chain"))
    if chain != "unavailable":
        result["masked_chain"] = chain
    scopes = ("masked", "nested") if chain == "unique_leaf" else ("masked",)
    for scope in scopes:
        for key in _MASKED_CONTROLLER_FIELDS:
            field = f"{scope}_{key}"
            if (
                key in {"neutral_busy_origin", "receipt_boundary"}
                and detail.get(f"{scope}_code") != "neutral_busy"
            ):
                continue
            if key == "broker_boundary" and (
                chain not in {"single", "unique_leaf"}
                or detail.get(f"{scope}_code") != "neutral_busy"
                or detail.get(f"{scope}_neutral_busy_origin")
                != "gate_expired_controller"
                or detail.get("masked_rpc_method")
                not in {"submit_chat", "submit_embedding"}
            ):
                continue
            if key in {
                "validation_boundary",
                "validation_field",
                "validation_type",
            } and (
                chain not in {"single", "unique_leaf"}
                or detail.get(f"{scope}_family") != "validation"
                or detail.get("masked_rpc_method")
                not in {"submit_chat", "submit_embedding"}
                or (
                    detail.get(f"{scope}_validation_boundary") == "chat_response"
                    and detail.get("masked_rpc_method") != "submit_chat"
                )
                or (
                    detail.get(f"{scope}_validation_boundary") == "embedding_response"
                    and detail.get("masked_rpc_method") != "submit_embedding"
                )
                or _emit_value(
                    f"{scope}_validation_boundary",
                    detail.get(f"{scope}_validation_boundary"),
                )
                == "unavailable"
                or (
                    key != "validation_boundary"
                    and (
                        _emit_value(
                            f"{scope}_validation_field",
                            detail.get(f"{scope}_validation_field"),
                        )
                        == "unavailable"
                        or _emit_value(
                            f"{scope}_validation_type",
                            detail.get(f"{scope}_validation_type"),
                        )
                        == "unavailable"
                    )
                )
            ):
                continue
            value = _emit_value(field, detail.get(field))
            if value != "unavailable":
                result[field] = value
    return result


def _emit_value(name, value):
    return (
        value if type(value) is str and value in _DRAIN_FIELDS[name] else "unavailable"
    )


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate evidence field")
        result[key] = value
    return result


def _holder_observation(stdout):
    prefix = "RD_NEUTRAL_HOLDER_V1:"
    rows = [
        line[len(prefix) :] for line in stdout.splitlines() if line.startswith(prefix)
    ]
    if len(rows) != 1 or len(rows[0]) > 300:
        return {}
    try:
        row = json.loads(rows[0], object_pairs_hook=_strict_object)
    except (ValueError, RecursionError):
        return {}
    if type(row) is not dict or row.keys() != {"lock_scope", "holder", "caller"}:
        return {}
    fields = {
        f"first_{key}": _emit_value(f"first_{key}", val) for key, val in row.items()
    }
    return fields if "unavailable" not in fields.values() else {}


def _rpc_send_deadline(fields):
    return (
        fields.get("code") == "deadline"
        and fields.get("stage") == "rpc_send"
        and type(fields.get("rpc_method")) is str
        and fields["rpc_method"] in _RPC_REPLY_METHODS
    )


def _duration(ms):
    for ceiling, label in (
        (50, "lt_50ms"),
        (250, "lt_250ms"),
        (1000, "lt_1s"),
        (5000, "lt_5s"),
    ):
        if ms < ceiling:
            return label
    return "ge_5s"


def _remaining(ms):
    if ms <= -250:
        return "late_250ms_or_more"
    if ms < 0:
        return "late_lt_250ms"
    if ms == 0:
        return "at_deadline"
    return "left_250ms_or_more" if ms >= 250 else "left_lt_250ms"


def _rpc_deadline_timing(stdout, fields):
    """Use only the composed fixture's existing private RPC timing capture."""
    if not _rpc_send_deadline(fields):
        return {}
    method = fields["rpc_method"]
    prefix = "RD_RPC_TIMING:"
    matches = [line for line in stdout.splitlines() if line.startswith(prefix)]
    if len(matches) != 1 or len(matches[0]) > 200_000:
        return {}
    try:
        report = json.loads(matches[0][len(prefix) :], object_pairs_hook=_strict_object)
    except (ValueError, RecursionError):
        return {}
    if type(report) is not dict:
        return {}
    tail = report.get("tail")
    if (
        type(tail) is not list
        or not 1 <= len(tail) <= 64
        or any(type(row) is not dict for row in tail)
    ):
        return {}

    def bounded(value, low, high):
        return type(value) is int and low <= value <= high

    candidates = [
        (i, row)
        for i, row in enumerate(tail)
        if row.get("method") == method
        and row.get("stage") == "send"
        and row.get("ok") is False
    ]
    # Do not select among repeated same-method failures; the scoped
    # failure marker does not contain an RPC ordinal to disambiguate.
    if len(candidates) != 1:
        return {}
    index, send = candidates[0]
    ordinal, sequence = send.get("rpc_ordinal"), send.get("sequence")
    if not (
        bounded(ordinal, 1, 1_000_000)
        and bounded(sequence, 0, 1_000_000)
        and bounded(send.get("remaining_ms"), -3_600_000, 0)
    ):
        return {}
    found = {}
    handler_index = None
    commit_indices = []
    for i, row in enumerate(tail):
        if row.get("method") == method and row.get("rpc_ordinal") == ordinal:
            stage = row.get("stage")
            if type(stage) is not str or stage not in {
                "auth",
                "handle",
                "commit",
                "send",
            }:
                continue
            if (
                not bounded(row.get("rpc_ordinal"), 1, 1_000_000)
                or not bounded(row.get("sequence"), 0, 1_000_000)
                or row["sequence"] != sequence
                or i > index
                or type(row.get("ok")) is not bool
                or not bounded(row.get("elapsed_ms"), 0, 3_600_000)
                or not bounded(row.get("remaining_ms"), -3_600_000, 3_600_000)
            ):
                return {}
            if stage == "handle":
                handler_index = i
            if stage == "commit":
                commit_indices.append(i)
            found.setdefault(stage, []).append(row)
    if len(found.get("send", ())) != 1 or (
        handler_index is not None and any(i > handler_index for i in commit_indices)
    ):
        return {}
    result = {
        "rpc_send_elapsed": _duration(send["elapsed_ms"]),
        "rpc_send_remaining": _remaining(send["remaining_ms"]),
    }
    for stage, name in (("auth", "auth"), ("handle", "handler")):
        rows = found.get(stage, ())
        if len(rows) > 1 or (rows and not rows[0]["ok"]):
            return {}
        if rows:
            result[f"rpc_{name}_elapsed"] = _duration(rows[0]["elapsed_ms"])
            if stage == "handle":
                result["rpc_handler_remaining"] = _remaining(rows[0]["remaining_ms"])
    rows = found.get("commit", ())
    if rows:
        result["rpc_commit_max_elapsed"] = _duration(
            max(row["elapsed_ms"] for row in rows)
        )
        late = [row for row in rows if row["remaining_ms"] < 0]
        crossing = [row for row in late if row["elapsed_ms"] + row["remaining_ms"] > 1]
        if (
            len(crossing) == 1
            and crossing[0]["ok"] is True
            and crossing[0].get("ownership") == "rpc_fenced_neutral"
        ):
            unit = crossing[0].get("unit")
            if type(unit) is str and unit in _RPC_LATE_COMMIT_UNITS:
                result["rpc_late_commit_unit"] = unit
        result["rpc_commit_deadline_cut"] = (
            "crossed"
            if crossing
            else "already_late"
            if late
            and all(row["elapsed_ms"] + row["remaining_ms"] < -1 for row in late)
            else "not_observed_past_deadline"
            if not late
            else None
        )
        if result["rpc_commit_deadline_cut"] is None:
            result.pop("rpc_commit_deadline_cut")
    return result


def _sdk_outcome(fields):
    return (
        fields.get("family") == "provider"
        and fields.get("code") == "provider_outcome_unknown"
        and fields.get("stage") == "provider"
        and fields.get("rpc_method") == "submit_embedding"
    )


def _drain_observations(stdout):
    """Only exact fixed vocabulary from the original fixture's private capture."""
    if type(stdout) is not str or len(stdout) > 4_000_000:
        return {}
    seen = {}
    prefixes = {"RD_QUEUE_FAILURE:": 8, "RD_QUEUE_STATE:": 1}
    for line in stdout.splitlines():
        prefix = next((p for p in prefixes if line.startswith(p)), None)
        if prefix is None:
            continue
        rows = seen.setdefault(prefix, [])
        if len(rows) >= prefixes[prefix] or len(line) > 16_384:
            return {}
        try:
            row = json.loads(line[len(prefix) :], object_pairs_hook=_strict_object)
        except (ValueError, RecursionError):
            return {}
        if type(row) is not dict:
            return {}
        rows.append(row)
    result = {}
    holder = _holder_observation(stdout)
    if holder:
        result.update(holder)
    failures = seen.get("RD_QUEUE_FAILURE:", [])
    if failures:
        result.update(
            {
                key: _emit_value(key, failures[0].get(key))
                for key in ("family", "code", "stage", "sqlstate", "rpc_method")
            }
        )
        if result.get("stage") == "observer":
            result["observer_failure"] = _emit_value(
                "observer_failure", failures[0].get("observer_failure")
            )
        if result.get("code") == "neutral_busy":
            for key in ("neutral_busy_origin", "receipt_boundary"):
                result[key] = _emit_value(key, failures[0].get(key))
        if _sdk_outcome(result):
            result["sdk_outcome_cut"] = _emit_value(
                "sdk_outcome_cut", failures[0].get("sdk_outcome_cut")
            )
        result.update(_masked_controller_observation(failures[0]))
    result.update(_rpc_deadline_timing(stdout, result))
    states = seen.get("RD_QUEUE_STATE:", [])
    if states:
        result["run"] = _emit_value("run", states[0].get("status"))
        units = states[0].get("units")
        if (
            states[0].get("unit_overflow") is False
            and type(units) is list
            and len(units) <= 16
            and all(type(unit) is dict for unit in units)
        ):
            for role in _ROLES:
                values = [
                    _emit_value(role, unit.get("state"))
                    for unit in units
                    if unit.get("role") == role
                ]
                if values and len(set(values)) == 1:
                    result[role] = values[0]
    return {
        key: val
        for key, val in result.items()
        if val != "unavailable" or key == "masked_state"
    }


def _bridge_phase(source, line):
    phase = _anchored_phase(
        source,
        line,
        BRIDGE,
        "bridge",
        occurrences={"drain(env.factory, env.settings)": 2},
    )
    if phase not in {
        "rd_drain",
        "rd_release_unreleased",
        "oe_premature_resume",
    }:
        return phase
    records = [entry.strip() for entry in source.splitlines()]
    if phase == "rd_release_unreleased":
        if records[line - 1 : line + 2] == [
            "assert (",
            'env.client.post(route + "/release", headers=env.foreign).status_code == 404',
            ")",
        ]:
            return "rd_release_foreign"
        return phase
    # A multiline assert starts before its comparison line. Mark the call
    # boundary from the exact four-line source statement, not exception text.
    comparisons = {
        "rd_drain": ("waiting_release", "rd_waiting_release"),
        "oe_premature_resume": ("child_owned_by_engine_worker", "oe_queue_boundary"),
    }
    state, target = comparisons[phase]
    release_checks = [
        i
        for i in range(len(records) - 3)
        if records[i : i + 4]
        == [
            "assert (",
            "drive_one(env.factory, env.settings, owner_id=owner, workflow_id=wid).state",
            f'== "{state}"',
            ")",
        ]
    ]
    if len(release_checks) == 1 and line >= release_checks[0] + 1:
        return target
    return phase


def _drain_verdict(root, name):
    module, test = CASES[name]
    case = _case(root / f"{name}.xml", module, test)
    if case is None or not any(case.find(s) is not None for s in ("failure", "error")):
        return {}
    fields = {}
    for key, allowed in (
        ("proof_phase", _OUTCOME_DETAIL_PHASES),
        *((f"rd_drain_{key}", allowed) for key, allowed in _DRAIN_FIELDS.items()),
    ):
        values = _properties(case, key)
        if len(values) == 1 and values[0] in allowed:
            fields[key.removeprefix("rd_drain_")] = values[0]
    detail = {}
    for key in list(fields):
        if key.startswith(("masked_", "nested_")):
            detail[{"masked_state": "state", "masked_chain": "chain"}.get(key, key)] = (
                fields.pop(key)
            )
    fields.update(
        _masked_controller_observation(
            {"code": fields.get("code"), "masked_controller": detail}
        )
    )
    if fields.get("proof_phase") not in _DRAIN_PHASES or not _sdk_outcome(fields):
        fields.pop("sdk_outcome_cut", None)
    if fields.get("stage") != "observer":
        fields.pop("observer_failure", None)
    if fields.get("code") != "neutral_busy":
        fields.pop("neutral_busy_origin", None)
        fields.pop("receipt_boundary", None)
    if fields.get("proof_phase") not in _RELEASE_PHASES:
        fields.pop("release_resource", None)
        fields.pop("release_status", None)
    if fields.get("proof_phase") not in _DRAIN_PHASES or not _rpc_send_deadline(fields):
        for key in _RPC_TIMING_FIELDS:
            fields.pop(key, None)
    if fields.get("rpc_commit_deadline_cut") != "crossed":
        fields.pop("rpc_late_commit_unit", None)
    if fields.get("proof_phase") != "oe_claim":
        for key in _OE_CLAIM_FIELDS:
            fields.pop(key, None)
    else:
        family = fields.get("oe_claim_error")
        if family:
            fields.pop("oe_claim_state", None)
        if fields.get("oe_claim_boundary") not in {None, "tick_call"}:
            for key in ("oe_claim_error", "oe_claim_site"):
                fields.pop(key, None)
            family = None
        if fields.get("oe_claim_boundary") == "readback_enter":
            fields.pop("oe_claim_state", None)
        if (
            family != "database"
            or fields.get("oe_claim_sqlstate") != "P0001"
            or fields.get("oe_claim_site") not in {"dispatch", "claim"}
        ):
            fields.pop("oe_claim_predicate", None)
        for suffix, expected in (
            ("domain", "domain"),
            ("sqlstate", "database"),
            ("model", "validation"),
        ):
            if family != expected:
                fields.pop(f"oe_claim_{suffix}", None)
    if (
        name != "h_bridge"
        or fields.get("proof_phase") != "oe_claim"
        or fields.get("oe_claim_error") != "database"
        or fields.get("oe_claim_sqlstate") != "P0001"
        or fields.get("oe_claim_site") != "dispatch"
        or fields.get("oe_claim_predicate") != "reverse_graph"
    ):
        for key in _OE_REVERSE_FIELDS:
            fields.pop(key, None)
    if fields.get("oe_reverse_root") not in {"active", "active_and_sibling"}:
        fields.pop("oe_reverse_attachment", None)
    return fields if "proof_phase" in fields and len(fields) > 1 else {}


def _anchored_phase(source, line, anchors, fallback, *, occurrences=None):
    records = source.splitlines()
    if not 1 <= line <= len(records):
        return fallback
    markers = {}
    for code, phase in anchors.items():
        indices = [i for i, val in enumerate(records, 1) if val.strip() == code]
        expected = (occurrences or {}).get(code, 1)
        if len(indices) != expected:
            return fallback
        markers[indices[-1]] = phase
    return next(
        (markers[key] for key in sorted(markers, reverse=True) if key <= line), fallback
    )


def _phase(item, call):
    if call.when != "call":
        return call.when
    module = Path(item.path).stem
    if module == CASES["heartbeat"][0]:
        return "heartbeat"
    for frame in reversed(call.excinfo.traceback):
        filename = Path(frame.path)
        if (
            filename.name == "workflow_structured_stage_fixture.py"
            and frame.name == "run_bridge"
        ):
            return _bridge_phase(
                str(frame.frame.code.source()),
                frame.lineno - frame.frame.code.firstlineno + 1,
            )
        if filename.name == f"{module}.py" and frame.name == item.originalname:
            source = str(frame.frame.code.source())
            line = frame.lineno - frame.frame.code.firstlineno + 1
            if module == CASES["restore"][0]:
                return _anchored_phase(source, line, RESTORE, "before_bridge")
            if module == CASES["h_bridge"][0]:
                return _anchored_phase(source, line, H_BRIDGE, "before_bridge")
            return _anchored_phase(
                source,
                line,
                {
                    "completed = run_bridge(env, source, tmp_path)": "bridge",
                    "call_counts = (len(env.rd_calls), len(env.oe_calls))": "current_graph_readback",
                },
                "before_bridge",
            )
    return "before_bridge"


def _release_details(line, values):
    path = values.get("path")
    if type(path) is not str:
        return {}
    root, sep, suffix = path.partition("/")
    resource = {"execution-snapshots": "snapshot", "analyses": "analysis"}.get(root)
    if not sep or len(suffix) != 36 or resource is None:
        return {}
    result = {"release_resource": resource}
    # Only an exact status assertion may observe a finalized response. In
    # particular, a pre-response exception cannot use an earlier loop result.
    assertion = {
        "assert unreleased.status_code == 409, unreleased.text": "unreleased",
        "assert released.status_code == 200, released.text": "released",
    }.get(line)
    if assertion:
        from httpx import Response

        response = values.get(assertion)
        if type(response) is Response and type(response.status_code) is int:
            code = f"http_{response.status_code}"
            result["release_status"] = (
                code if code in _DRAIN_FIELDS["release_status"] else "other"
            )
    return result


def _release_observation(call):
    for frame in reversed(call.excinfo.traceback):
        if (
            Path(frame.path).name == "workflow_structured_stage_fixture.py"
            and frame.name == "run_bridge"
        ):
            source = str(frame.frame.code.source())
            line = frame.lineno - frame.frame.code.firstlineno
            records = source.splitlines()
            if 0 <= line < len(records):
                return _release_details(records[line].strip(), frame.locals)
    return {}


def _oe_claim_observation(call):
    from psycopg.errors import RaiseException
    from pydantic import ValidationError
    from sqlalchemy.exc import DBAPIError

    from backend.app import research_workflow_oe_claim as claims
    from backend.app.services import DomainError
    from workers.research import workflow_tick

    value = call.excinfo.value
    fixture = Path("tests/integration/workflow_structured_stage_fixture.py").resolve()
    for frame in reversed(call.excinfo.traceback):
        if Path(frame.path).resolve() != fixture or frame.name != "run_bridge":
            continue
        source = str(frame.frame.code.source()).splitlines()
        index = frame.lineno - frame.frame.code.firstlineno
        if not (0 <= index < len(source)):
            return {}
        line = source[index].strip()
        if line == 'assert oe_claim.state == "structured_child_queued"':
            result = frame.locals.get("oe_claim")
            if (
                type(result) is workflow_tick.TickOutcome
                and type(result.state) is str
                and result.state in _OE_CLAIM_FIELDS["oe_claim_state"]
            ):
                return {
                    "oe_claim_boundary": "tick_result",
                    "oe_claim_state": result.state,
                }
            return {"oe_claim_boundary": "tick_result"}
        if (
            line == "with env.factory() as session:"
            and index > 0
            and source[index - 1].strip()
            == 'assert oe_claim.state == "structured_child_queued"'
        ):
            return {"oe_claim_boundary": "readback_enter"}
        if BRIDGE.get(line) == "oe_claim":
            break
        return {}
    else:
        return {}

    result = {"oe_claim_boundary": "tick_call", "oe_claim_error": "other"}
    if type(value) is DomainError:
        result["oe_claim_error"] = "domain"
        if (
            type(value.code) is str
            and value.code in _OE_CLAIM_FIELDS["oe_claim_domain"]
        ):
            result["oe_claim_domain"] = value.code
    elif isinstance(value, DBAPIError):
        result["oe_claim_error"] = "database"
        code = getattr(value.orig, "sqlstate", None)
        if type(code) is str and code in _OE_CLAIM_FIELDS["oe_claim_sqlstate"]:
            result["oe_claim_sqlstate"] = code
        if code == "P0001" and type(value.orig) is RaiseException:
            primary = value.orig.diag.message_primary
            if type(primary) is str:
                predicate = _OE_CLAIM_TRIGGER_LABELS.get(primary)
                if predicate is not None:
                    result["oe_claim_predicate"] = predicate
    elif type(value) is ValidationError:
        result["oe_claim_error"] = "validation"
        if (
            type(value.title) is str
            and value.title in _OE_CLAIM_FIELDS["oe_claim_model"]
        ):
            result["oe_claim_model"] = value.title

    sites = (
        (workflow_tick.drive_one.__code__, "tick"),
        (workflow_tick._read.__code__, "read"),
        (claims.dispatch_oe_stage.__code__, "dispatch"),
        (claims.claim_oe_stage.__code__, "claim"),
    )
    trace = value.__traceback__
    site = None
    for _ in range(64):
        if trace is None:
            if site is not None:
                result["oe_claim_site"] = site
            if site not in {"dispatch", "claim"}:
                result.pop("oe_claim_predicate", None)
            return result
        for code, label in sites:
            if trace.tb_frame.f_code is code:
                site = label
        trace = trace.tb_next
    return result


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if report.failed and call.excinfo is not None:
        try:
            phase = _phase(item, call)
        except (OSError, TypeError, ValueError, AttributeError):
            phase = None  # Reporter faults never replace the original test failure.
        if phase in PHASES:
            item.user_properties.append(("proof_phase", phase))
        if phase in _DRAIN_PHASES and call.when == "call":
            for key, value in _drain_observations(report.capstdout).items():
                item.user_properties.append((f"rd_drain_{key}", value))
        if phase in _RELEASE_PHASES and call.when == "call":
            try:
                for key, value in _release_observation(call).items():
                    item.user_properties.append((f"rd_drain_{key}", value))
            except (OSError, TypeError, ValueError, AttributeError):
                pass  # Preserve the original failed assertion.
        if phase == "oe_claim" and call.when == "call":
            try:
                for key, value in _oe_claim_observation(call).items():
                    item.user_properties.append((f"rd_drain_{key}", value))
            except (OSError, TypeError, ValueError, AttributeError):
                pass  # Preserve the original failed assertion.
        error = call.excinfo.value
        if type(error).__module__ == "backend.app.services" and type(
            error
        ).__name__ == ("DomainError"):
            code = getattr(error, "code", None)
            if type(code) is str and code in CODES:
                item.user_properties.append(("proof_code", code))
    return report


def _properties(case, key):
    return [
        prop.get("value")
        for prop in case.findall("./properties/property")
        if prop.get("name") == key
    ]


def _read_cases(path):
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 20_000_000:
            return []
        return list(ET.fromstring(path.read_bytes()).iter("testcase"))
    except (OSError, ET.ParseError):
        return []


def _case(path, module, test):
    cases = _read_cases(path)
    if len(cases) != 1:
        return None
    case = cases[0]
    if (case.get("classname"), case.get("name")) != (
        f"tests.integration.{module}",
        test,
    ):
        return None
    return case


def coordination(root):
    expected = {
        f"{_COORDINATION_TEST}[{parameter}]": field
        for parameter, field in _COORDINATION_CASES.items()
    }
    unavailable = dict.fromkeys(expected.values(), "unavailable")
    cases = _read_cases(root / "coordination.xml")
    if len(cases) != len(expected) or any(
        case.get("classname") != _COORDINATION_CLASS for case in cases
    ):
        return unavailable
    actual = {}
    for case in cases:
        field = expected.get(case.get("name"))
        if field is None or field in actual:
            return unavailable
        statuses = [
            s for s in ("failure", "error", "skipped") if case.find(s) is not None
        ]
        if len(statuses) > 1:
            return unavailable
        actual[field] = statuses[0] if statuses else "passed"
    return {field: actual[field] for field in expected.values()}


def outcome(root, name):
    module, test = CASES[name]
    case = _case(root / f"{name}.xml", module, test)
    if case is None:
        return "unavailable", "unavailable", "unavailable"
    statuses = [s for s in ("failure", "error", "skipped") if case.find(s) is not None]
    if len(statuses) > 1:
        return "unavailable", "unavailable", "unavailable"
    status = statuses[0] if statuses else "passed"
    if (
        name == "h_bridge"
        and status == "passed"
        and any(
            _properties(case, milestone) != ["passed"] for milestone in _H_MILESTONES
        )
    ):
        return "unavailable", "unavailable", "unavailable"
    phase = "complete" if status == "passed" else "unavailable"
    code = "unavailable"
    if status in {"failure", "error"}:
        phases = PHASES if name == "h_bridge" else PHASES - {"current_cold_restore"}
        for key, allowed in (("proof_phase", phases), ("proof_code", CODES)):
            values = _properties(case, key)
            if len(values) == 1 and values[0] in allowed:
                if key == "proof_phase":
                    phase = values[0]
                else:
                    code = values[0]
    return status, phase, code


def _report_case(root, name):
    status, phase, code = outcome(root, name)
    print(f"RD_PRODUCTION_HEARTBEAT_V1 {name}={status} phase={phase} code={code}")
    if name in {"bridge", "h_bridge", "restore"}:
        fields = _drain_verdict(root, name)
        if fields:
            detail = " ".join(f"{key}={fields[key]}" for key in sorted(fields))
            print(f"RD_PRODUCTION_RD_DRAIN_V1 case={name} {detail}")
    return status != "passed"


def main():
    parser = ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--require-pass", choices=(*CASES, "coordination"))
    mode.add_argument("--report-h-bridge", action="store_true")
    mode.add_argument("--report-historical", action="store_true")
    args = parser.parse_args()
    if args.require_pass == "coordination":
        return int(any(value != "passed" for value in coordination(ROOT).values()))
    if args.require_pass:
        return int(outcome(ROOT, args.require_pass)[0] != "passed")
    if args.report_h_bridge:
        return int(_report_case(ROOT, "h_bridge"))
    outcomes = coordination(ROOT)
    fields = " ".join(f"{key}={value}" for key, value in outcomes.items())
    print(f"RD_PRODUCTION_RPC_COORDINATION_V1 {fields}")
    failed = any(value != "passed" for value in outcomes.values())
    for name in CASES:
        if not args.report_historical or name != "h_bridge":
            failed |= _report_case(ROOT, name)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
