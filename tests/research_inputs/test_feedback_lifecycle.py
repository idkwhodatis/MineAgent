"""Transaction/publication decision tests; no installed028/PG acceptance claim."""

from contextlib import contextmanager
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import research_input_feedback as service
from backend.app.research_input_storage import verify_payload
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.qlib_adapter.objective import DirectObjectiveMeasurement
from packages.research_inputs.contracts import SourceOutputRef
from packages.research_inputs.feedback import (
    FeedbackProjectionDefinition,
    project_direct_feedback,
)


def fixture(monkeypatch):
    owner, workflow, stageid, child = uuid4(), uuid4(), uuid4(), uuid4()
    definition = FeedbackProjectionDefinition(
        source=SourceOutputRef(
            workflow_id=workflow,
            stage_id=uuid4(),
            child_run_id=child,
            output_kind="direct_analysis",
            output_id=uuid4(),
            content_sha256="a" * 64,
        )
    )
    stage = NS(
        id=stageid,
        workflow_id=workflow,
        kind="experiment_feedback_projection",
        definition_canonical=canonical_bytes(definition).decode(),
        definition_sha256=digest(definition),
    )
    root = NS(status="running")
    row = NS(
        id=uuid4(),
        owner_id=owner,
        workflow_id=workflow,
        stage_id=stageid,
        operation=stage.kind,
        source_canonical=stage.definition_canonical,
        source_sha256=stage.definition_sha256,
        mapping_canonical=None,
        mapping_sha256=None,
        raw_object_id=None,
        raw_object_sha256=None,
        source_workflow_id=workflow,
        source_stage_id=definition.source.stage_id,
        source_child_run_id=child,
    )
    events = [NS(id=uuid4(), state="prepared"), NS(id=uuid4(), state="started")]
    monkeypatch.setattr(
        service, "current_feedback_stage", lambda *a: (root, stage, definition)
    )
    monkeypatch.setattr(service, "events_for", lambda *a: tuple(events))
    writes = []
    session = NS(get=lambda *a: row, add=writes.append, flush=lambda: None)

    def append(session, row, state, **kwargs):
        value = NS(id=uuid4(), state=state, **kwargs)
        events.append(value)
        return value

    monkeypatch.setattr(service, "append_event", append)
    return owner, workflow, stageid, definition, root, row, events, session, writes


def measured():
    return DirectObjectiveMeasurement(
        declaration_sha256="b" * 64,
        protocol_sha256="c" * 64,
        result_sha256="a" * 64,
        status="failed",
        reason="threshold_not_satisfied",
        value=-0.25,
        defined_days=1,
        total_pairs=3,
        defined_pairs=2,
    )


def test_finish_revalidates_actual_release_before_retaining_projection(monkeypatch):
    owner, w, s, definition, _root, row, events, session, writes = fixture(monkeypatch)
    call = []
    ids = (uuid4(), uuid4())
    read = object()
    admission = object()

    def revalidate(*args, **kwargs):
        assert args[-1] is read and kwargs["admission"] is admission
        call.append("revalidated")
        return NS(measurement=measured()), ids

    def publish(*args, **kwargs):
        assert call == ["revalidated"]
        assert args[2] == "normalized_feedback"
        assert args[3] == canonical_bytes(
            project_direct_feedback(definition, measured(), ids)
        )
        call.append("published")
        return NS(id=uuid4())

    monkeypatch.setattr(service, "revalidate_feedback_read", revalidate)
    monkeypatch.setattr(service, "publish_input_object", publish)
    result = service.finish_feedback(
        session, owner, w, s, row.id, events[-1].id, NS(), read, admission=admission
    )
    assert result.state == "succeeded" and len(result.outputs) == 1 and len(writes) == 2
    assert call == ["revalidated", "published"]
    assert (
        "failed" in writes[1].content_canonical
        and "-0.25" in writes[1].content_canonical
    )


def test_denied_release_never_publishes_or_emits_success(monkeypatch):
    owner, w, s, _, _, row, events, session, writes = fixture(monkeypatch)

    def blocked(*a, **k):
        raise DomainError("workflow_feedback_release_required", "Unavailable", 409)

    monkeypatch.setattr(service, "revalidate_feedback_read", blocked)
    monkeypatch.setattr(
        service,
        "publish_input_object",
        lambda *a, **k: pytest.fail("unauthorized output"),
    )
    with pytest.raises(DomainError):
        service.finish_feedback(
            session,
            owner,
            w,
            s,
            row.id,
            events[-1].id,
            NS(),
            object(),
            admission=object(),
        )
    assert not writes and len(events) == 2


@pytest.mark.parametrize(
    "fault",
    ["wrong_start", "already_finished", "source_child", "mapping", "foreign_owner"],
)
def test_finish_requires_original_claim_and_exact_source(monkeypatch, fault):
    owner, w, s, _, _, row, events, session, _ = fixture(monkeypatch)
    started = events[-1].id
    if fault == "wrong_start":
        started = uuid4()
    elif fault == "already_finished":
        events.append(NS(id=uuid4(), state="succeeded"))
    elif fault == "source_child":
        row.source_child_run_id = uuid4()
    elif fault == "mapping":
        row.mapping_canonical = "{}"
    elif fault == "foreign_owner":
        row.owner_id = uuid4()
    monkeypatch.setattr(
        service,
        "revalidate_feedback_read",
        lambda *a, **k: pytest.fail("invalid claimant"),
    )
    with pytest.raises(DomainError):
        service.finish_feedback(
            session, owner, w, s, row.id, started, NS(), object(), admission=object()
        )


@pytest.mark.parametrize(
    "status,expected",
    [
        ("running", "started"),
        ("pause_requested", "started"),
        ("cancel_requested", "cancelled"),
    ],
)
def test_prepared_control_is_honest_without_disclosure(monkeypatch, status, expected):
    owner, w, s, _, root, row, events, session, _ = fixture(monkeypatch)
    events.pop()
    root.status = status
    monkeypatch.setattr(
        service,
        "prepare_feedback_read",
        lambda *a, **k: pytest.fail("no read during start/cancel"),
    )
    result = service.start_feedback(session, owner, w, s, row.id)
    assert result.state == expected
    if expected == "cancelled":
        assert result.error_code == "owner_cancelled"


def test_existing_started_cannot_be_adopted(monkeypatch):
    owner, w, s, _, _, row, _, session, _ = fixture(monkeypatch)
    with pytest.raises(DomainError):
        service.start_feedback(session, owner, w, s, row.id)


def test_feedback_payload_uses_separate_canonical_grammar(monkeypatch):
    _, _, _, definition, *_ = fixture(monkeypatch)
    output = project_direct_feedback(definition, measured(), (uuid4(), uuid4()))
    raw = canonical_bytes(output)
    assert verify_payload("normalized_feedback", raw) == output.sha256
    with pytest.raises(ValueError):
        verify_payload("normalized_external", raw)
    with pytest.raises(ValueError):
        verify_payload("normalized_feedback", raw + b"\n")


def test_runner_cancel_has_no_disclose_publication_or_fake_start(monkeypatch):
    owner, w, s, _, root, row, events, _, _ = fixture(monkeypatch)
    events.pop()
    root.status = "cancel_requested"
    log = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def commit(self):
            log.append("commit")

        def rollback(self):
            log.append("rollback")

        def get(self, *a):
            return row

    factory = NS(kw={"bind": object()})

    class Factory:
        kw = factory.kw

        def __call__(self):
            return Session()

    @contextmanager
    def lifecycle(*a):
        yield

    monkeypatch.setattr(service, "shared_lifecycle", lifecycle)
    monkeypatch.setattr(service, "prepare_feedback", lambda *a: row)
    monkeypatch.setattr(
        service,
        "prepare_feedback_read",
        lambda *a, **k: pytest.fail("cancel must not release"),
    )
    monkeypatch.setattr(
        service, "publication", lambda *a, **k: pytest.fail("cancel must not publish")
    )
    result = service.run_feedback_stage(
        Factory(), NS(storage_root="/unused"), owner_id=owner, workflow_id=w, stage_id=s
    )
    assert result.state == "cancelled" and log == ["commit"]


def test_feedback_real_retained_bytes_count_in_same_inventory(monkeypatch, tmp_path):
    import os
    import time

    from backend.app.research_input_storage import (
        input_object_inventory,
        read_input_object,
    )

    owner, _, _, definition, *_ = fixture(monkeypatch)
    output = project_direct_feedback(definition, measured(), (uuid4(), uuid4()))
    raw = canonical_bytes(output)
    key = f"factors/{owner}/input-objects/{output.sha256}.feedback.json"
    path = tmp_path / key
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    row = NS(
        owner_id=owner,
        kind="normalized_feedback",
        sha256=output.sha256,
        size_bytes=len(raw),
        storage_key=key,
    )
    assert read_input_object(tmp_path, row, owner) == raw
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert input_object_inventory(
            fd, deadline=time.monotonic() + 10, maximum_files=1
        ) == (len(raw), 1)
        path.write_bytes(raw + b" ")
        with pytest.raises(ValueError):
            input_object_inventory(fd, deadline=time.monotonic() + 10, maximum_files=1)
    finally:
        os.close(fd)


def test_terminal_prepared_cancel_needs_no_result_or_exposure(monkeypatch):
    from backend.app import research_input_storage

    owner, w, s, definition, _, row, events, session, _ = fixture(monkeypatch)
    stage = NS(
        id=s,
        workflow_id=w,
        kind="experiment_feedback_projection",
        definition_canonical=canonical_bytes(definition).decode(),
        definition_sha256=digest(definition),
    )
    from backend.app.models import utcnow
    from packages.research_inputs.derivation import DerivationEvent

    events[:] = [
        DerivationEvent(
            id=uuid4(),
            derivation_id=row.id,
            ordinal=1,
            state="prepared",
            recorded_at=utcnow(),
        ),
        DerivationEvent(
            id=uuid4(),
            derivation_id=row.id,
            ordinal=2,
            state="cancelled",
            error_code="owner_cancelled",
            recorded_at=utcnow(),
        ),
    ]
    session.scalar = lambda *a: row
    session.scalars = lambda *a: NS(all=list)
    monkeypatch.setattr(
        service, "_binding", lambda *a: (None, stage, None, definition, None, None)
    )
    monkeypatch.setattr(
        research_input_storage, "require_publication_admission", lambda *a: None
    )
    monkeypatch.setattr(
        service,
        "revalidate_feedback_read",
        lambda *a, **k: pytest.fail("cancel has no values"),
    )
    actual = service.validate_feedback_terminal(
        session, owner, w, s, NS(), prepared_read=None, admission=123456789.0
    )
    assert (
        actual.state == "cancelled"
        and actual.outputs == ()
        and actual.objects == ()
        and actual.exposure_ids == ()
    )
    assert actual.terminal_event_id == events[
        -1
    ].id and actual.terminal_event_sha256 == digest(events[-1])


def test_historical_terminal_cannot_invent_native_guard(tmp_path):
    from backend.app.factor_native_io import NativeStorageError

    with pytest.raises(NativeStorageError):
        service.validate_feedback_terminal_under_native_guard(
            NS(), uuid4(), uuid4(), uuid4(), NS(storage_root=tmp_path)
        )
