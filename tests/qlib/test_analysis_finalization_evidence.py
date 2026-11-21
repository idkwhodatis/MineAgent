"""Configured finalization consumes one closed analysis read per transaction."""

import json
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import factor_analysis_results as results
from backend.app import rdagent_analysis_finalization as finalization
from backend.app import rdagent_configured_evaluation as configured
from backend.app import rdagent_operational_finalization as operational
from backend.app.execution_snapshot_models import ExecutionSnapshotPublication
from backend.app.factor_analysis_models import FactorDirectAnalysisIntent
from backend.app.rdagent_models import FactorEvaluationStart, FactorGenerationSource
from runtimes.rdagent_operational.rpc import RPCError


def test_feedback_derives_fields_once_without_sharing_evidence_across_transactions(
    monkeypatch,
):
    start, source, evaluation, publication, intent, version = [
        NS(id=uuid4()) for _ in range(6)
    ]
    fence = NS(owner_id=uuid4(), run_id=uuid4(), attempt_id=uuid4(), generation=1)
    peer, settings, factory = object(), object(), object()
    source.start_id, source.source_sha256 = start.id, "c" * 64
    evaluation.evaluated_source_id = source.id
    evaluation.start_id = start.id
    evaluation.factor_version_id = version.id
    evaluation.input_sha256 = "a" * 64
    evaluation.snapshot_input = NS(
        window=NS(start=date(2024, 1, 1), end=date(2024, 1, 2))
    )
    snapshot = NS(
        snapshot_id=publication.id,
        evaluation_id=evaluation.id,
        source_id=source.id,
        generation_start_id=start.id,
        factor_version_id=version.id,
        input_sha256=evaluation.input_sha256,
        output_sha256="b" * 64,
        manifest_sha256="d" * 64,
        rows=[NS(value=None), NS(value=1)],
    )
    sessions, readers, published = [], [], []
    second_hash = "f" * 64

    def scalar(query):
        if query.column_descriptions[0]["entity"] is ExecutionSnapshotPublication:
            return publication
        assert query.column_descriptions[0]["entity"] is FactorDirectAnalysisIntent
        return intent

    @contextmanager
    def transaction(received_factory, received_fence, received_settings):
        assert (received_factory, received_fence, received_settings) == (
            factory,
            fence,
            settings,
        )
        session = NS(scalar=scalar, scalars=lambda _: [NS(id=uuid4())])
        sessions.append(session)
        yield session

    def scope_row(session, entity, identity, received_fence):
        assert session is sessions[-1] and received_fence is fence
        candidate = {
            FactorGenerationSource: source,
            FactorEvaluationStart: evaluation,
        }[entity]
        assert identity == candidate.id
        return candidate

    def read(session, received_fence, analysis_id, received_settings):
        assert session is sessions[-1] and received_fence is fence
        assert analysis_id == intent.id and received_settings is settings
        readers.append(session)
        return snapshot, results.VerifiedDirectAnalysis(
            analysis_id=intent.id,
            snapshot_id=snapshot.snapshot_id,
            admission_id=uuid4(),
            result_sha256="e" * 64 if len(sessions) == 1 else second_hash,
            measurement_sha256="a" * 64,
            result=None,
            measurement=NS(status="passed", reason="threshold_satisfied"),
        )

    def publish(received_factory, received_fence, received_settings, kind, raw, key):
        assert (received_factory, received_fence, received_settings) == (
            factory,
            fence,
            settings,
        )
        assert kind == "factor_generation_feedback"
        assert key == f"rd-execution-feedback:{evaluation.id}"
        doc = finalization.parse_document(kind, raw)
        published.append(doc)
        return doc.analysis_id

    monkeypatch.setattr(operational, "snapshot_transaction", transaction)
    monkeypatch.setattr(operational, "_scope_row", scope_row)
    monkeypatch.setattr(operational, "_publish", publish)
    monkeypatch.setattr(operational, "_call_members", lambda *a: [object()])
    monkeypatch.setattr(
        operational,
        "_completed_member_response",
        lambda *a: (None, NS(content=json.dumps({"final_decision": True}))),
    )
    monkeypatch.setattr(configured, "require_rd_controller", lambda *a: None)
    monkeypatch.setattr(configured, "_scope_row", scope_row)
    monkeypatch.setattr(configured, "SnapshotInput", NS(model_validate=lambda x: x))
    monkeypatch.setattr(configured, "_read_verified_analysis_with_snapshot", read)
    monkeypatch.setattr(results, "_read_verified_analysis_with_snapshot", read)

    def finish():
        return finalization.retain_analysis_feedback(
            factory,
            fence,
            peer,
            start_id=start.id,
            source_id=source.id,
            evaluation_id=evaluation.id,
            upstream_feedback={
                "execution": None,
                "return_checking": None,
                "code": None,
                "final_decision": True,
            },
            settings=settings,
        )

    assert finish() == finish() == intent.id
    assert readers == sessions and len(sessions) == 2
    assert published[0].analysis_result_sha256 == "e" * 64
    assert published[1].analysis_result_sha256 == second_hash
    assert all(doc.owner_id == fence.owner_id for doc in published)

    snapshot.generation_start_id = uuid4()
    with pytest.raises(RPCError, match="configured_snapshot_binding"):
        finish()
    assert len(published) == 2

    # Generation may not validate the previous receipt against a new read.
    expected = {
        "analysis_id": intent.id,
        "analysis_result_sha256": second_hash,
        "objective_measurement_sha256": "a" * 64,
        "objective_status": "passed",
        "objective_reason": "threshold_satisfied",
    }
    finalization._verify_fields(published[1], expected)
    with pytest.raises(RPCError, match="analysis_feedback_measurement_drift"):
        finalization._verify_fields(published[0], expected)
