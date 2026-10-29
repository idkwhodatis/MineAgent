"""Status projection doubles; immutable child validator has separate graph tests."""

from datetime import UTC
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import rdagent_workflow_saved as saved
from backend.app import research_workflow_rd_history as service
from backend.app.services import DomainError
from tests.rdagent.test_rdagent_workflow_child_contracts import config


@pytest.mark.parametrize(
    "status,attempt,operation,evidence,expected",
    [
        ("queued", False, False, False, "selected_not_submitted"),
        ("running", True, True, False, "not_observed"),
        ("failed", True, True, True, "not_observed"),
        ("queued", False, False, True, "not_observed"),
    ],
)
def test_real_status_is_not_prompt_stop_or_success_evidence(
    monkeypatch, status, attempt, operation, evidence, expected
):
    value = config()
    binding = value.workflow_inputs.binding
    root = NS(id=binding.workflow_id)
    stage = NS(
        id=binding.stage_id,
        kind="rd_factor_generation",
        ordinal=1,
        child_run_id=value.workflow_child_run_id,
        input_revision_id=binding.input_revision_id,
        definition_sha256=binding.definition_sha256,
    )
    receipt = NS(
        id=uuid4(), workflow_id=root.id, stage_id=stage.id, run_id=stage.child_run_id
    )
    child_attempt = NS(id=uuid4(), run_id=stage.child_run_id, generation=1)
    run = NS(
        id=stage.child_run_id,
        owner_id=binding.owner_id,
        status=status,
        generation=1,
        active_attempt_id=child_attempt.id if attempt else None,
        config=value.model_dump(mode="json"),
    )
    rows = iter(
        [
            receipt,
            run,
            uuid4() if operation else None,
            uuid4() if evidence else None,
            None,
        ]
    )
    session = NS(
        scalar=lambda q: next(rows),
        scalars=lambda q: [child_attempt] if attempt else [],
    )
    monkeypatch.setattr(saved, "verify_workflow_submission", lambda *a: receipt)
    result = service.read_rd_stage_history(session, binding.owner_id, root, stage, None)
    assert result.source_status == status and result.consumption == expected
    assert result.model_requests == () and result.output_refs == ()
    assert result.containment == result.settlement == "not_observed"
    assert result.finished_at is None


def test_missing_actual_child_receipt_is_not_queued(monkeypatch):
    session = NS(scalar=lambda q: None)
    with pytest.raises(DomainError):
        service.read_rd_stage_history(
            session, uuid4(), NS(id=uuid4()), NS(id=uuid4()), None
        )


@pytest.mark.parametrize("change", [None, "missing_stop", "request", "orphan"])
def test_actual_checkpoint_exposes_only_verified_output_and_first_request(
    monkeypatch, tmp_path, change
):
    from datetime import datetime

    from backend.app import research_workflow_rd_checkpoint
    from backend.app.factor_native_io import require_native_storage_guard
    from backend.app.research_broker_models import ResearchModelOperation

    value = config()
    binding = value.workflow_inputs.binding
    root = NS(id=binding.workflow_id)
    stage = NS(
        id=binding.stage_id,
        kind="rd_factor_generation",
        ordinal=1,
        child_run_id=value.workflow_child_run_id,
        input_revision_id=binding.input_revision_id,
        definition_sha256=binding.definition_sha256,
    )
    receipt = NS(
        id=uuid4(), workflow_id=root.id, stage_id=stage.id, run_id=stage.child_run_id
    )
    now = datetime.now(UTC)
    attempt = NS(
        id=uuid4(),
        run_id=stage.child_run_id,
        generation=1,
        started_at=now,
        finished_at=now,
    )
    run = NS(
        id=stage.child_run_id,
        owner_id=binding.owner_id,
        status="succeeded",
        generation=1,
        active_attempt_id=attempt.id,
        config=value.model_dump(mode="json"),
    )
    checkpoint_id = uuid4()
    facts = NS(
        stage_id=stage.id,
        child_run_id=run.id,
        submission_id=receipt.id,
        input_revision_id=stage.input_revision_id,
        input_sha256=binding.input_sha256,
        definition_sha256=stage.definition_sha256,
        attempt_id=attempt.id,
        generation=1,
        analysis_id=uuid4(),
        analysis_result_sha256="b" * 64,
    )
    operation = NS(
        id=uuid4(),
        owner_id=binding.owner_id,
        run_id=run.id,
        attempt_id=attempt.id,
        generation=1,
        request_sha256="c" * 64,
    )
    transport = NS(
        id=uuid4(),
        owner_id=binding.owner_id,
        run_id=run.id,
        attempt_id=attempt.id,
        generation=1,
        operation_id=operation.id,
        ordinal=1,
    )
    inclusion = NS(
        owner_id=binding.owner_id,
        workflow_id=root.id,
        stage_id=stage.id,
        run_id=run.id,
        attempt_id=attempt.id,
        generation=1,
        input_revision_id=stage.input_revision_id,
        input_sha256=value.workflow_inputs_sha256,
        operation_id=operation.id,
        transport_id=transport.id,
        request_sha256="d" * 64 if change == "request" else operation.request_sha256,
    )
    scalar_rows = iter((receipt, run, operation.id, uuid4(), checkpoint_id))
    many = iter(
        ([attempt], [inclusion, inclusion] if change == "orphan" else [inclusion])
    )
    session = NS(
        scalar=lambda q: next(scalar_rows),
        scalars=lambda q: next(many),
        get=lambda cls, identity: (
            operation if cls is ResearchModelOperation else transport
        ),
    )
    monkeypatch.setattr(saved, "verify_workflow_submission", lambda *a: receipt)
    (tmp_path / "factors").mkdir()

    def verify(*a):
        require_native_storage_guard(tmp_path)
        if change == "missing_stop":
            raise DomainError("workflow_waiting_containment", "No positive stop", 409)
        return NS(id=checkpoint_id), facts

    monkeypatch.setattr(research_workflow_rd_checkpoint, "verify_rd_checkpoint", verify)

    def read():
        return service.read_rd_stage_history(
            session, binding.owner_id, root, stage, NS(storage_root=tmp_path)
        )

    if change:
        with pytest.raises(DomainError):
            read()
    else:
        result = read()
        assert (
            result.checkpoint_id == checkpoint_id
            and result.containment == "positive_stop"
        )
        assert (
            result.settlement == "settled"
            and result.output_refs[0].output_id == facts.analysis_id
        )
        assert result.model_requests[0].request_sha256 == operation.request_sha256
        assert result.consumption == "included_in_submitted_prompt"
        assert result.automatic_feedback_refs == ()
