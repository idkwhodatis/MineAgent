"""Versioned final graph links only; preceding physical analysis closure separate."""

import copy
from uuid import uuid4

import pytest
from test_rdagent_execution_links import graph  # noqa: F401

from backend.app.rdagent_execution_links import validate_execution_generation_links
from packages.factors.contracts import canonical_bytes, digest
from packages.rdagent_adapter.analysis_feedback import RDAnalysisFeedback
from packages.rdagent_adapter.analysis_object_codec import parse_document
from packages.rdagent_adapter.execution_outcomes import parse_document as parse_v1


@pytest.fixture
def analysis_graph(graph):  # noqa: F811
    rd, ex, rs, files = graph
    rd["schema_version"] = "mineagent.rd-source-restore-closure.v4"
    ex["schema_version"] = "mineagent.execution-snapshot-restore-closure.v2"
    rs["schema_version"] = "mineagent.research-restore-closure.v7"
    result = ex["tables"]["factor_execution_evaluation_outcomes"][0]
    scope = {
        key: result[key] for key in ("owner_id", "run_id", "attempt_id", "generation")
    }
    aid = str(uuid4())
    measurement = {"status": "passed", "reason": "threshold_satisfied"}
    direct = {
        "schema_version": "mineagent.direct-analysis-restore-closure.v1",
        "tables": {
            "factor_analysis_plans": [
                {"owner_id": scope["owner_id"], "run_id": scope["run_id"]}
            ],
            "factor_direct_analysis_intents": [
                {"id": aid, **scope, "snapshot_id": result["snapshot_id"]}
            ],
            "factor_direct_analysis_outcomes": [
                {
                    "analysis_id": aid,
                    **scope,
                    "result_sha256": "e" * 64,
                    "measurement_sha256": digest(measurement),
                    "measurement": measurement,
                }
            ],
        },
    }
    feedback = next(
        x
        for x in rs["tables"]["research_objects"]
        if x["object_kind"] == "factor_generation_feedback"
    )
    body = copy.deepcopy(files[feedback["storage_key"]])
    body.pop("objective_passed")
    body.pop("objective_protocol")
    body.update(
        schema_version="mineagent.rd-execution-feedback.v2",
        analysis_id=aid,
        analysis_result_sha256="e" * 64,
        objective_measurement_sha256=digest(measurement),
        objective_status="passed",
        objective_reason="threshold_satisfied",
    )
    doc = RDAnalysisFeedback.model_validate_json(canonical_bytes(body))
    body = doc.model_dump(mode="json")
    files[feedback["storage_key"]] = body
    feedback.update(sha256=digest(body), size_bytes=len(canonical_bytes(body)))
    result["feedback_sha256"] = feedback["sha256"]
    adoption = next(
        x
        for x in rs["tables"]["research_objects"]
        if x["object_kind"] == "factor_adoption_receipt"
    )
    files[adoption["storage_key"]]["feedback_sha256"] = feedback["sha256"]
    adoption.update(
        sha256=digest(files[adoption["storage_key"]]),
        size_bytes=len(canonical_bytes(files[adoption["storage_key"]])),
    )
    rd["tables"]["factor_generation_outcomes"][0]["adoption_sha256"] = adoption[
        "sha256"
    ]
    return graph, direct


def validate(value, direct=None):
    data, analysis = value
    rd, ex, rs, files = data
    return validate_execution_generation_links(
        rd,
        ex,
        rs,
        files.__getitem__,
        1000,
        direct_analysis_closure=analysis if direct is None else direct,
    )


def test_v4_exact_stopped_result_links(analysis_graph):
    assert validate(analysis_graph) is True


@pytest.mark.parametrize(
    "field", ["result_sha256", "measurement_sha256", "snapshot_id", "generation"]
)
def test_actual_analysis_binding_drift(analysis_graph, field):
    _, direct = analysis_graph
    if field == "snapshot_id":
        direct["tables"]["factor_direct_analysis_intents"][0][field] = str(uuid4())
    elif field == "generation":
        direct["tables"]["factor_direct_analysis_outcomes"][0][field] = 2
    else:
        direct["tables"]["factor_direct_analysis_outcomes"][0][field] = "a" * 64
    with pytest.raises(ValueError):
        validate(analysis_graph)


def test_v4_requires_analysis_closure(analysis_graph):
    data, _ = analysis_graph
    rd, ex, rs, files = data
    with pytest.raises(ValueError):
        validate_execution_generation_links(rd, ex, rs, files.__getitem__, 1000)


def test_v2_cannot_enter_old_codec(analysis_graph):
    data, _ = analysis_graph
    obj = next(
        x
        for x in data[2]["tables"]["research_objects"]
        if x["object_kind"] == "factor_generation_feedback"
    )
    raw = canonical_bytes(data[3][obj["storage_key"]])
    assert type(parse_document("factor_generation_feedback", raw)) is RDAnalysisFeedback
    with pytest.raises(ValueError):
        parse_v1("factor_generation_feedback", raw)
    with pytest.raises(ValueError):
        parse_document("factor_generation_feedback", b" " + raw)
    with pytest.raises(ValueError):
        parse_document("factor_generation_feedback", raw[:-1] + b',"row_count":3}')


@pytest.mark.parametrize(
    "revision", ["021_execution_snapshots", "020_rd_controller", "023_future", None]
)
def test_v2_writer_requires_exact022(analysis_graph, revision):
    from types import SimpleNamespace

    from backend.app.research_object_services import _versioned_identity
    from backend.app.services import DomainError

    data, _ = analysis_graph
    obj = next(
        x
        for x in data[2]["tables"]["research_objects"]
        if x["object_kind"] == "factor_generation_feedback"
    )
    raw = canonical_bytes(data[3][obj["storage_key"]])
    session = SimpleNamespace(
        scalars=lambda sql: SimpleNamespace(
            all=lambda: [] if revision is None else [revision]
        )
    )
    with pytest.raises(DomainError):
        _versioned_identity(session, uuid4(), "factor_generation_feedback", raw)


def test_v2_writer_exactownerandhash(analysis_graph):
    from types import SimpleNamespace
    from uuid import UUID

    from backend.app.research_object_services import _versioned_identity

    data, _ = analysis_graph
    obj = next(
        x
        for x in data[2]["tables"]["research_objects"]
        if x["object_kind"] == "factor_generation_feedback"
    )
    raw = canonical_bytes(data[3][obj["storage_key"]])
    session = SimpleNamespace(
        scalars=lambda sql: SimpleNamespace(all=lambda: ["022_factor_analysis"])
    )
    identity = _versioned_identity(
        session, UUID(obj["owner_id"]), "factor_generation_feedback", raw
    )
    assert identity.sha256 == obj["sha256"]
    with pytest.raises(ValueError):
        _versioned_identity(session, uuid4(), "factor_generation_feedback", raw)
