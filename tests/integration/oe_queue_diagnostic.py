"""Test-only closed OE task failure/state observer; never restarts a claimed run."""

import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from submission_sql_diagnostic import classify

from backend.app.services import DomainError
from packages.rdagent_provider.transport import ProviderFailure
from packages.research_runtime.process_contracts import ProcessError

CODES = {
    "oe_structured_socket_owner",
    "oe_structured_controller_launch_authority",
    "oe_structured_controller_expired_before_start",
    "oe_structured_population_identity",
    "provider_timeout",
    "oe_structured_event_key",
    "oe_structured_controller_revision",
    "oe_structured_controller_stop_busy",
    "oe_structured_protected_peer",
    "oe_structured_socket_canonical",
    "oe_structured_engine_input_drift",
    "provider_containment_unconfirmed",
    "oe_structured_event_not_installed",
    "oe_structured_socket_scope",
    "oe_structured_controller_root_identity",
    "oe_structured_checkpoint_completion",
    "oe_structured_image_parent",
    "oe_structured_checkpoint_chunk",
    "oe_structured_controller_deadline",
    "oe_structured_controller_already_admitted",
    "oe_structured_socket_eof",
    "oe_structured_controller_child_not_running",
    "oe_structured_controller_profile",
    "oe_structured_controller_staged_bytes_changed",
    "oe_structured_engine_already_admitted",
    "oe_structured_socket_event",
    "oe_structured_controller_expired",
    "oe_structured_controller_stop_identity",
    "neutral_transaction_lost",
    "original_controller_deadline",
    "oe_structured_controller_child_identity",
    "oe_structured_final_publication",
    "oe_structured_claim_drift",
    "oe_structured_socket_deadline",
    "oe_structured_controller_reservation",
    "oe_structured_socket_evaluation",
    "oe_structured_candidate_bound",
    "oe_structured_sampling_identity",
    "oe_structured_evaluation_outcome",
    "oe_structured_controller_child_survived",
    "oe_structured_controller_cleanup_root_changed",
    "oe_structured_controller_bootstrap",
    "oe_structured_socket_reused",
    "oe_structured_original_lease",
    "oe_structured_seed_peer",
    "oe_structured_controller_changed_before_start",
    "oe_structured_seed_source",
    "oe_structured_engine_dependency_lock",
    "oe_structured_completion_before_stop",
    "oe_structured_runtime_drift",
    "oe_structured_completion_chain",
    "oe_structured_controller_ordinal",
    "oe_structured_controller_leader_changed",
    "oe_structured_socket_reply_bound",
    "oe_structured_controller_expired_before_create",
    "oe_structured_socket_request_drift",
    "oe_structured_bootstrap_drift",
    "oe_structured_evaluation_identity",
    "oe_structured_controller_stopped_immutable",
    "oe_structured_controller_daemon_stop_uncertain",
    "oe_structured_engine_plan_drift",
    "oe_structured_controller_cleanup_identity",
    "provider_secret_unavailable",
    "oe_structured_controller_kernel_deadline_exceeded",
    "oe_structured_controller_staged_root_changed",
    "oe_structured_evaluation_ticket_key",
    "oe_structured_engine_retained_input_drift",
    "oe_structured_engine_candidate_drift",
    "oe_structured_controller_missing_intent",
    "oe_structured_controller_staged_file_changed",
    "oe_structured_socket_bound",
    "oe_structured_controller_journal_drift",
    "oe_structured_socket_descriptor",
    "oe_structured_controller_expired_peer",
    "oe_structured_controller_not_running",
    "oe_structured_controller_intent",
    "oe_structured_evaluation_epoch",
    "oe_structured_event_payload",
    "oe_structured_controller_scope",
    "oe_structured_controller_settlement_drift",
    "oe_structured_candidate_event",
    "oe_structured_controller_staged_layout_changed",
    "oe_structured_controller_settlement_state",
    "oe_structured_controller_receipt",
    "oe_structured_event_source",
    "oe_structured_parent_identity",
    "oe_structured_controller_stop_requires_recovery",
    "oe_structured_controller_journal_identity",
    "oe_structured_engine_binding",
    "oe_structured_rendered_digest",
    "oe_structured_controller_journal_intent",
    "oe_structured_evaluation_grant",
    "oe_structured_controller_process",
    "oe_structured_controller_staged_socket_changed",
    "oe_structured_controller_already_claimed",
    "oe_structured_controller_stop_omits_retained_child",
    "oe_structured_first_event_final",
    "oe_structured_controller_journal_admission",
    "oe_structured_engine_schema",
    "oe_structured_event_response",
    "oe_structured_controller_absence_uncertain",
    "oe_structured_controller_already_settled",
    "oe_structured_checkpoint_epoch",
    "neutral_busy",
    "oe_structured_controller_connection_reused",
    "oe_structured_controller_not_claimed",
    "oe_structured_controller_claim_changed",
    "provider_outcome_unknown",
    "oe_structured_image_inventory",
    "oe_structured_terminal_identity",
    "oe_structured_socket_request",
    "oe_structured_socket_response",
    "oe_structured_evaluation_ack",
    "oe_structured_controller_cleanup_inventory",
}
STAGES = {
    ("workers.research.oe_structured_queue", "execute"): "queue",
    ("workers.research.oe_structured_queue", "claim_structured_run"): "claim",
    ("workers.research.oe_structured_controller", "image_inventory"): "image_inventory",
    (
        "workers.research.oe_structured_controller",
        "execute_controller_epoch",
    ): "controller",
    (
        "backend.app.oe_structured_epoch_services",
        "prepare_controller_epoch",
    ): "epoch_prepare",
    (
        "backend.app.oe_structured_epoch_services",
        "expected_controller_bootstrap",
    ): "bootstrap",
    ("runtimes.openevolve_structured_host.supervisor", "start"): "controller_start",
    (
        "runtimes.openevolve_structured_host.supervisor",
        "accept_controller",
    ): "controller_accept",
    (
        "runtimes.openevolve_structured_host.supervisor",
        "_finish_once",
    ): "controller_finish",
    (
        "runtimes.openevolve_structured_host.supervisor",
        "_complete_stop",
    ): "controller_stop",
    ("runtimes.openevolve_structured_host.peer", "require_peer"): "peer",
    ("backend.app.oe_structured_socket_server", "serve_controller"): "serve",
    ("backend.app.oe_structured_socket_server", "_read_exact"): "socket_read",
    ("backend.app.oe_structured_socket_server", "write_frame"): "socket_write",
    ("backend.app.oe_structured_socket_server", "_event"): "event",
    ("backend.app.oe_structured_provider_broker", "submit_provider_chat"): "provider",
    ("workers.research.oe_structured_finish", "finish_structured_run"): "finish",
    (
        "backend.app.oe_structured_completion_services",
        "complete_stopped_epoch",
    ): "completion",
    ("backend.app.research_lifecycle", "neutral_transaction"): "transaction",
    ("backend.app.oe_structured_events", "apply_event"): "event_apply",
    ("backend.app.oe_structured_events", "_proposal"): "proposal",
    ("backend.app.oe_structured_event_wire", "read_event"): "event_parse",
    ("backend.app.oe_structured_socket_services", "authenticate_event"): "authenticate",
    (
        "backend.app.oe_structured_socket_services",
        "handle_provider_socket",
    ): "provider_socket",
    ("backend.app.oe_structured_socket_services", "_bound_call"): "provider_binding",
    (
        "backend.app.oe_structured_evaluation_services",
        "register_evaluation",
    ): "evaluation_admit",
    (
        "backend.app.oe_structured_evaluation_services",
        "execute_evaluation",
    ): "evaluation_execute",
    (
        "backend.app.oe_structured_evaluation_services",
        "read_evaluation_response",
    ): "evaluation_read",
    (
        "backend.app.oe_structured_engine_services",
        "prepare_execution",
    ): "engine_prepare",
    (
        "backend.app.oe_structured_engine_process",
        "execute_candidate_engine",
    ): "engine_execute",
    ("backend.app.oe_structured_provider_broker", "_settle"): "provider_settle",
    (
        "backend.app.oe_structured_controller_process_services",
        "claim_controller_intent",
    ): "controller_claim",
    (
        "backend.app.oe_structured_controller_process_services",
        "_ingest",
    ): "controller_receipt",
    (
        "backend.app.oe_structured_controller_process_services",
        "record_controller_completion",
    ): "controller_completion",
    (
        "backend.app.oe_structured_terminal_services",
        "register_terminal_application",
    ): "terminal_apply",
    (
        "backend.app.oe_structured_terminal_services",
        "register_worker_outcome",
    ): "worker_outcome",
    (
        "backend.app.oe_structured_completion_services",
        "_published_checkpoints",
    ): "checkpoint_closure",
    (
        "backend.app.oe_structured_completion_services",
        "_stopped_controller",
    ): "controller_closure",
    (
        "backend.app.oe_structured_completion_services",
        "_stopped_engine",
    ): "engine_closure",
    (
        "backend.app.research_lifecycle",
        "finish_oe_structured_transaction",
    ): "terminal_commit",
    (
        "workers.research.oe_structured_finish",
        "_validate_current_success",
    ): "finish_validate",
    (
        "runtimes.openevolve_structured_host.supervisor",
        "verify_container",
    ): "controller_verify",
    (
        "runtimes.openevolve_structured_host.supervisor",
        "verify_image_inventory",
    ): "image_verify",
}
STATUSES = {
    "queued",
    "preparing",
    "running",
    "finalizing",
    "succeeded",
    "failed",
    "cancelled",
    "interrupted",
    "paused",
    "cancel_requested",
}
ROLES = {"openevolve_orchestrator", "native_evaluator"}
PROCESS_STATES = {"admitted", "launch_intended", "uncertain", "running", "stopped"}

VALUE_CATEGORIES = {
    "Controller capabilities drift": "controller_capabilities_drift",
    "Controller closed readonly mount inventory drift": "controller_closed_readonly_mount_inventory_drift",
    "Controller committed input/ticket differs": "controller_committed_input_ticket_differs",
    "Controller exact runtime/image identity drift": "controller_exact_runtime_image_identity_drift",
    "Controller identity changed during observation": "controller_identity_changed_during_observation",
    "Controller implicit mount/process/limit drift": "controller_implicit_mount_process_limit_drift",
    "Controller namespace/resource drift": "controller_namespace_resource_drift",
    "Controller proc/sys protections drift": "controller_proc_sys_protections_drift",
    "Controller report channel bound/stream differs": "controller_report_channel_bound_stream_differs",
    "Controller report child identity changed": "controller_report_child_identity_changed",
    "Controller report shape/order differs": "controller_report_shape_order_differs",
    "Controller security profile drift": "controller_security_profile_drift",
    "Controller ulimit drift": "controller_ulimit_drift",
    "Duplicate/oversize controller report": "controller_duplicate_oversize_controller_report",
    "Exact controller image configuration required": "controller_exact_controller_image_configuration_required",
    "Exact created container ID required": "controller_exact_created_container_id_required",
    "Exact verified image required": "controller_exact_verified_image_required",
    "Missing trusted started report": "controller_missing_trusted_started_report",
    "Pinned Python base inventory required": "controller_pinned_python_base_inventory_required",
    "Truncated controller report": "controller_truncated_controller_report",
}
VALIDATION_TYPES = {
    "string_pattern_mismatch",
    "float_type",
    "json_invalid",
    "string_type",
    "literal_error",
    "greater_than_equal",
    "value_error",
    "finite_number",
    "extra_forbidden",
    "bool_type",
    "less_than_equal",
    "model_type",
    "less_than",
    "missing",
    "int_type",
    "greater_than",
}


def failure(error):
    family = {
        ProcessError: "process",
        DomainError: "domain",
        ProviderFailure: "provider",
        ValueError: "value",
        ValidationError: "validation",
        TypeError: "type",
        KeyError: "key",
        AttributeError: "attribute",
        TimeoutError: "timeout",
    }.get(type(error), "other")
    code = getattr(error, "code", None)
    result = {
        "family": family,
        "code": code if type(code) is str and code in CODES else "unknown",
        "stage": "unknown",
    }
    if type(error) is ValueError:
        message = error.args[0] if len(error.args) == 1 else None
        result["category"] = (
            VALUE_CATEGORIES.get(message, "other") if type(message) is str else "other"
        )
    if type(error) is ValidationError:
        count = error.error_count()
        result["validation_count"] = min(count, 10000)
        result["validation_overflow"] = count > 32
        result["validation_types"] = (
            sorted(
                {
                    item["type"] if item["type"] in VALIDATION_TYPES else "other"
                    for item in error.errors(
                        include_url=False, include_context=False, include_input=False
                    )
                }
            )[:8]
            if count <= 32
            else []
        )
    if isinstance(error, SQLAlchemyError):
        result["family"] = "orm"
    if isinstance(error, DBAPIError):
        result["family"] = "database"
        result["database"] = classify(SimpleNamespace(original_exception=error.orig))
    trace = error.__traceback__
    for _ in range(128):
        if trace is None:
            break
        key = (trace.tb_frame.f_globals.get("__name__"), trace.tb_frame.f_code.co_name)
        result["stage"] = STAGES.get(key, result["stage"])
        trace = trace.tb_next
    return result


def project_state(run, attempts, units):
    return {
        "status": run.status
        if run is not None and run.status in STATUSES
        else "unknown",
        "attempt_overflow": len(attempts) > 8,
        "attempts": [
            {
                "status": a.status if a.status in STATUSES else "unknown",
                "error": "none"
                if a.error is None
                else (
                    a.error if type(a.error) is str and a.error in CODES else "other"
                ),
                "stopped": a.sandbox_stopped is True,
                "exit_code": a.exit_code
                if type(a.exit_code) is int and 0 <= a.exit_code <= 255
                else None,
            }
            for a in attempts[:8]
        ],
        "unit_overflow": len(units) > 16,
        "units": sorted(
            [
                {
                    "role": role if role in ROLES else "other",
                    "state": state if state in PROCESS_STATES else "unknown",
                }
                for role, state in units[:16]
            ],
            key=lambda v: (v["role"], v["state"]),
        ),
    }


def queue_state(factory, run_id):
    from uuid import UUID

    from backend.app.models import ExecutionAttempt, ResearchRun
    from backend.app.research_process_models import (
        ResearchProcessAdmission,
        ResearchProcessState,
    )

    identity = UUID(str(run_id))
    with factory() as session:
        run = session.get(ResearchRun, identity)
        attempts = session.scalars(
            select(ExecutionAttempt).filter_by(run_id=identity).limit(9)
        ).all()
        units = session.execute(
            select(ResearchProcessAdmission.role, ResearchProcessState.status)
            .outerjoin(
                ResearchProcessState,
                ResearchProcessState.admission_id == ResearchProcessAdmission.id,
            )
            .where(ResearchProcessAdmission.run_id == identity)
            .limit(17)
        ).all()
        return project_state(run, attempts, units)


def emit(marker, value):
    try:
        print(marker + json.dumps(value, sort_keys=True), flush=True)
    except Exception:  # noqa: BLE001, S110 -- observation cannot mask task failure
        pass


@contextmanager
def diagnose_oe_queue(factory, run_id):
    from workers.research import oe_structured_queue

    original, failures = oe_structured_queue.execute, 0

    def execute(*args, **kwargs):
        nonlocal failures
        try:
            return original(*args, **kwargs)
        except BaseException as error:
            if failures < 8:
                failures += 1
                try:
                    result = failure(error)
                except Exception:  # noqa: BLE001 -- preserve original exception
                    result = {"family": "other", "code": "unknown", "stage": "unknown"}
                emit("OE_QUEUE_FAILURE_V1:", result)
            raise

    try:
        with patch.object(oe_structured_queue, "execute", execute):
            yield
    finally:
        try:
            state = queue_state(factory, run_id)
        except Exception:  # noqa: BLE001 -- read-only observation may fail
            state = {"status": "diagnostic_unavailable"}
        emit("OE_QUEUE_STATE_V1:", state)
