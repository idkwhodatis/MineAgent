"""Actual journal/body bytes with metadata doubles, not a whole PG restore."""

from types import SimpleNamespace as NS
from uuid import UUID

import pytest

from backend.app.rdagent_numeric_failure_backup import FIELDS, validate_failure_links
from packages.factors.contracts import canonical_bytes, digest
from packages.rdagent_adapter.numeric_failure import (
    RDNumericFailureFeedback,
    RDNumericFailureFrame,
)
from tests.research_runtime.test_numeric_rejection import retained


def uid(n):
    return str(UUID(int=n))


@pytest.fixture
def graph(tmp_path):
    admission, launch, identity, stop, rejection, _ = retained(tmp_path)
    scope = {
        "owner_id": str(admission.owner_id),
        "run_id": str(admission.run_id),
        "attempt_id": str(admission.attempt_id),
        "generation": admission.generation,
    }
    frame = RDNumericFailureFrame(
        source_id=uid(1),
        evaluation_id=uid(2),
        factor_version_id=uid(3),
        cutoff_execution_id=str(admission.logical_operation_id),
        candidate_admission_id=str(admission.id),
        rejection_receipt_id=uid(5),
        stop_receipt_id=uid(6),
        rejection_sha256=digest(rejection),
        stop_sha256=digest(stop),
        outcome=rejection.outcome,
        child_exit_code=rejection.child_exit_code,
    )
    doc = RDNumericFailureFeedback(
        **scope,
        start_id=uid(7),
        controller_launch_id=uid(8),
        final_decision_call_id=uid(10),
        failure=frame,
        code_sha256="a" * 64,
        input_sha256="b" * 64,
        upstream_feedback=canonical_bytes(
            {
                "execution": frame.feedback_text,
                "return_checking": None,
                "code": "actual critique",
                "final_decision": False,
            }
        ).decode(),
    )
    rows = {
        name: []
        for name in (
            "factor_numeric_failure_feedbacks",
            "factor_evaluation_outcomes",
            "factor_evaluation_starts",
            "factor_generation_sources",
            "factor_cutoff_executions",
            "factor_controller_launches",
            "factor_upstream_calls",
            "factor_upstream_call_outcomes",
            "factor_generation_model_operations",
            "factor_generation_outcomes",
        )
    }
    shared = {
        name: []
        for name in (
            "research_objects",
            "research_process_receipts",
            "research_model_operations",
            "research_model_transports",
            "research_model_outcomes",
        )
    }

    def add(table, **values):
        row = {**scope, **values}
        (rows if table in rows else shared)[table].append(row)
        return row

    bodies = {}

    def obj(id, kind, payload):
        raw = canonical_bytes(payload)
        sha = digest(payload)
        path = f"research-objects/{scope['owner_id']}/by-kind/{kind}/{sha}.json"
        add(
            "research_objects",
            id=id,
            object_kind=kind,
            sha256=sha,
            storage_key=path,
            size_bytes=len(raw),
        )
        bodies[path] = raw
        return sha

    sha = obj(uid(11), "factor_generation_feedback", doc)
    member = {key: None for key in FIELDS.split()}
    member.update(
        **scope,
        id=uid(12),
        evaluation_id=uid(2),
        evaluation_outcome_id=uid(13),
        cutoff_execution_id=str(admission.logical_operation_id),
        candidate_admission_id=str(admission.id),
        controller_launch_id=uid(8),
        final_decision_call_id=uid(10),
        rejection_receipt_id=uid(5),
        rejection_kind="rejection",
        rejection_sha256=digest(rejection),
        stop_receipt_id=uid(6),
        stop_kind="stop",
        stop_sha256=digest(stop),
        feedback_object_id=uid(11),
        feedback_object_kind="factor_generation_feedback",
        feedback_sha256=sha,
    )
    rows["factor_numeric_failure_feedbacks"].append(member)
    add(
        "factor_evaluation_outcomes",
        id=uid(13),
        evaluation_id=uid(2),
        status="execution_failed",
        snapshot_id=None,
        snapshot_input_sha256=None,
        snapshot_output_sha256=None,
        feedback_object_id=uid(11),
        feedback_sha256=sha,
    )
    add(
        "factor_evaluation_starts",
        id=uid(2),
        evaluated_source_id=uid(1),
        start_id=uid(7),
        factor_version_id=uid(3),
        expected_cutoffs=1,
        input_sha256="b" * 64,
    )
    add(
        "factor_generation_sources",
        id=uid(1),
        start_id=uid(7),
        factor_version_id=uid(3),
        source_sha256="a" * 64,
    )
    add(
        "factor_cutoff_executions",
        id=str(admission.logical_operation_id),
        evaluation_id=uid(2),
        evaluated_source_id=uid(1),
        start_id=uid(7),
        factor_version_id=uid(3),
        cutoff_ordinal=0,
        candidate_admission_id=str(admission.id),
    )
    add("factor_controller_launches", id=uid(8))
    for id, kind, value in ((uid(5), "rejection", rejection), (uid(6), "stop", stop)):
        add(
            "research_process_receipts",
            id=id,
            admission_id=str(admission.id),
            kind=kind,
            payload_sha256=digest(value),
        )
    for offset, phase, content in (
        (20, "critique", "actual critique"),
        (30, "final_decision", '{"final_decision":false}'),
    ):
        call = uid(9) if phase == "critique" else uid(10)
        add("factor_upstream_calls", id=call, start_id=uid(7), phase=phase, kind="chat")
        add(
            "factor_upstream_call_outcomes",
            id=uid(offset),
            call_id=call,
            status="completed",
            controller_launch_id=uid(8),
            member_count=1,
        )
        add(
            "factor_generation_model_operations",
            id=uid(offset + 1),
            call_id=call,
            operation_id=uid(offset + 2),
        )
        add(
            "research_model_operations",
            id=uid(offset + 2),
            protocol="mineagent.rdagent-provider-chat.v1",
            state="completed",
            request={"messages": [{"content": frame.feedback_text}]},
        )
        add(
            "research_model_transports",
            id=uid(offset + 3),
            operation_id=uid(offset + 2),
            state="completed",
        )
        response_sha = obj(uid(offset + 4), "model_response", {"content": content})
        add(
            "research_model_outcomes",
            id=uid(offset + 5),
            transport_id=uid(offset + 3),
            outcome="completed",
            response_object_id=uid(offset + 4),
            response_sha256=response_sha,
        )
    rd = {"schema_version": "mineagent.rd-source-restore-closure.v8", "tables": rows}
    research = {
        "schema_version": "mineagent.research-restore-closure.v13",
        "tables": shared,
    }
    native = {"tables": {"factor_native_publications": []}}
    execution = {"tables": {"factor_execution_snapshot_publications": []}}
    units = {str(admission.id): (launch, identity, stop, rejection)}

    def check():
        return validate_failure_links(
            rd,
            research,
            native,
            execution,
            units,
            lambda path, *_: bodies[path],
            max_rows=100,
        )

    return NS(
        check=check,
        rd=rd,
        shared=shared,
        rows=rows,
        native=native,
        execution=execution,
        units=units,
        bodies=bodies,
    )


def test_actual_rejection_and_feedback_bytes_match_without_snapshot(graph):
    graph.check()


@pytest.mark.parametrize(
    "change",
    [
        "missing_stop",
        "legacy",
        "snapshot",
        "adoption",
        "critique",
        "unbound_result",
        "body",
    ],
)
def test_cold_failure_rejects_detachment_or_success_alias(graph, change):
    if change == "missing_stop":
        graph.units.clear()
    elif change == "legacy":
        graph.rd["schema_version"] = "mineagent.rd-source-restore-closure.v5"
    elif change == "snapshot":
        graph.execution["tables"]["factor_execution_snapshot_publications"].append(
            {"id": uid(99), "evaluation_id": uid(2)}
        )
    elif change == "adoption":
        graph.rows["factor_generation_outcomes"].append(
            {"id": uid(99), "start_id": uid(7)}
        )
    elif change == "critique":
        graph.shared["research_model_operations"][0]["request"]["messages"][0][
            "content"
        ] = "other"
    elif change == "unbound_result":
        graph.rows["factor_numeric_failure_feedbacks"].clear()
    else:
        path = next(iter(graph.bodies))
        graph.bodies[path] = b"{}"
    with pytest.raises((ValueError, KeyError)):
        graph.check()
