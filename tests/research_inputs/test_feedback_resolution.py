"""Fresh release seam + real retained bytes; source engine itself is not mocked proof."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from test_feedback_lifecycle import fixture, measured

from backend.app import research_input_feedback_resolution as resolver
from backend.app.research_input_models import (
    ResearchInputDerivation,
    ResearchInputObject,
    ResearchInputRecord,
    ResearchInputVersion,
)
from backend.app.research_workflow_models import ResearchWorkflowStage
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.research_inputs.contracts import InputVersionRef
from packages.research_inputs.feedback import project_direct_feedback


def setup(monkeypatch, tmp_path):
    owner, w, s, definition, _, derivation, _, _, _ = fixture(monkeypatch)
    ids = (uuid4(), uuid4())
    output = project_direct_feedback(definition, measured(), ids)
    raw = canonical_bytes(output)
    record_id, version_id, obj_id = uuid4(), uuid4(), uuid4()
    ref = InputVersionRef(
        origin=output.origin,
        record_id=record_id,
        version_id=version_id,
        content_sha256=output.sha256,
    )
    record = NS(
        id=record_id,
        owner_id=owner,
        origin=ref.origin,
        disabled=False,
        current_version_id=version_id,
    )
    version = NS(
        id=version_id,
        owner_id=owner,
        record_id=record_id,
        derivation_id=derivation.id,
        item_ordinal=0,
        object_id=obj_id,
        content_sha256=output.sha256,
        content_canonical=raw.decode(),
    )
    stage = NS(
        id=s,
        owner_id=owner,
        workflow_id=w,
        kind="experiment_feedback_projection",
        child_run_id=None,
        definition_canonical=canonical_bytes(definition).decode(),
        definition_sha256=digest(definition),
    )
    key = f"factors/{owner}/input-objects/{output.sha256}.feedback.json"
    path = tmp_path / key
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    obj = NS(
        id=obj_id,
        owner_id=owner,
        kind="normalized_feedback",
        sha256=output.sha256,
        storage_key=key,
        size_bytes=len(raw),
    )
    rows = {
        ResearchInputRecord: record,
        ResearchInputVersion: version,
        ResearchInputDerivation: derivation,
        ResearchWorkflowStage: stage,
        ResearchInputObject: obj,
    }
    session = NS(get=lambda model, identity: rows[model])
    events = (
        NS(state="prepared"),
        NS(state="started"),
        NS(state="succeeded", outputs=(ref,)),
    )
    monkeypatch.setattr(resolver, "events_for", lambda *a: events)
    log = []
    actual = object()

    def read(*a):
        log.append("release-checked-before-source-bytes")
        return actual

    def revalidate(*a):
        assert a[-1] is actual
        log.append("ledger-revalidated")
        return NS(measurement=measured()), ids

    monkeypatch.setattr(resolver, "read_released_feedback_in_session", read)
    monkeypatch.setattr(resolver, "revalidate_released_feedback", revalidate)
    return owner, ref, rows, session, NS(storage_root=tmp_path), path, log, output


def test_real_feedback_bytes_recomputed_and_historical_disable_not_reinterpreted(
    monkeypatch, tmp_path
):
    owner, ref, rows, session, settings, _, log, output = setup(monkeypatch, tmp_path)
    rows[ResearchInputRecord].disabled = True
    with pytest.raises(DomainError):
        resolver.resolve_feedback_version(
            session, owner, ref, settings, historical=False
        )
    assert not log
    actual = resolver.resolve_feedback_version(
        session, owner, ref, settings, historical=True
    )
    assert actual.text == output.text and actual.ref == ref
    assert log == ["release-checked-before-source-bytes", "ledger-revalidated"]


@pytest.mark.parametrize(
    "fault",
    [
        "owner",
        "origin",
        "source",
        "stage",
        "definition",
        "object_kind",
        "bytes",
        "version_body",
        "current_head",
    ],
)
def test_fresh_substitution_rejected(monkeypatch, tmp_path, fault):
    owner, ref, rows, session, settings, path, _, _ = setup(monkeypatch, tmp_path)
    if fault == "owner":
        rows[ResearchInputVersion].owner_id = uuid4()
    elif fault == "origin":
        rows[ResearchInputRecord].origin = "normalized_external_proposal"
    elif fault == "source":
        rows[ResearchInputDerivation].source_child_run_id = uuid4()
    elif fault == "stage":
        rows[ResearchWorkflowStage].child_run_id = uuid4()
    elif fault == "definition":
        rows[ResearchWorkflowStage].definition_sha256 = "f" * 64
    elif fault == "object_kind":
        rows[ResearchInputObject].kind = "normalized_external"
    elif fault == "bytes":
        path.write_bytes(b"changed")
    elif fault == "version_body":
        rows[ResearchInputVersion].content_canonical = "{}"
    elif fault == "current_head":
        rows[ResearchInputRecord].current_version_id = uuid4()
    with pytest.raises((DomainError, ValueError)):
        resolver.resolve_feedback_version(
            session, owner, ref, settings, historical=False
        )


def test_missing_release_does_not_read_retained_feedback_text(monkeypatch, tmp_path):
    owner, ref, _, session, settings, _, _, _ = setup(monkeypatch, tmp_path)

    def blocked(*a):
        raise DomainError("workflow_feedback_release_required", "Unavailable", 409)

    monkeypatch.setattr(resolver, "read_released_feedback_in_session", blocked)
    monkeypatch.setattr(
        resolver,
        "read_input_object",
        lambda *a, **k: pytest.fail("read before release"),
    )
    with pytest.raises(DomainError):
        resolver.resolve_feedback_version(
            session, owner, ref, settings, historical=True
        )
