"""Final graph link tests with actual fixture rows/typed documents, not DB/runtime."""

import copy
from datetime import date
from uuid import uuid4

import pytest

from backend.app.rdagent_execution_links import validate_execution_generation_links
from packages.factors.contracts import DateWindow, canonical_bytes, digest
from packages.factors.fixtures import demo_fixture
from packages.rdagent_adapter.execution_outcomes import (
    RDExecutionAdoption,
    RDExecutionFeedback,
    RDExecutionProposal,
)
from packages.rdagent_adapter.fixture_objective import measured_fixture_objective


def uid():
    return str(uuid4())


@pytest.fixture
def graph():
    fixture = demo_fixture(factor_version_id=uuid4(), dataset_version_id=uuid4())
    inp = fixture.manifest.input.model_copy(
        update={
            "window": DateWindow(start=date(2024, 3, 15), end=date(2024, 3, 16)),
        }
    )
    rows = tuple(r for r in fixture.rows if r.session_date == date(2024, 3, 15))
    assert measured_fixture_objective(inp, rows)
    scope = {"owner_id": uid(), "run_id": uid(), "attempt_id": uid(), "generation": 1}
    (
        start,
        source,
        evaluation,
        snapshot,
        assembly,
        boundary,
        observation,
        controller,
        reservation,
    ) = [uid() for _ in range(9)]
    common = {
        **scope,
        "start_id": start,
        "source_id": source,
        "evaluation_id": evaluation,
        "snapshot_id": snapshot,
        "factor_version_id": str(inp.version.factor_version_id),
        "code_sha256": inp.version.code_sha256,
        "input_sha256": inp.sha256,
        "output_sha256": "b" * 64,
        "manifest_sha256": "c" * 64,
    }
    rd = {"schema_version": "mineagent.rd-source-restore-closure.v3", "tables": {}}
    ex = {
        "schema_version": "mineagent.execution-snapshot-restore-closure.v1",
        "tables": {},
    }
    rs = {
        "schema_version": "mineagent.research-restore-closure.v6",
        "tables": {
            "research_objects": [],
            "research_object_publications": [],
            "research_object_outcomes": [],
            "research_budget_reservations": [
                {
                    **scope,
                    "id": reservation,
                    "state": "settled",
                    "amounts": {"mutation_slots": 1},
                }
            ],
            "research_budget_settlements": [
                {
                    **scope,
                    "id": uid(),
                    "reservation_id": reservation,
                    "outcome": "conservative_unknown",
                    "charge": {"mutation_slots": 1},
                }
            ],
        },
    }
    files = {}

    def artifact(kind, doc):
        body = doc.model_dump(mode="json")
        identity = uid()
        key = "research/" + identity + ".json"
        obj = {
            "id": identity,
            "owner_id": scope["owner_id"],
            "object_kind": kind,
            "size_bytes": len(canonical_bytes(body)),
            "sha256": digest(body),
            "storage_key": key,
        }
        publication = {**scope, "id": uid()}
        rs["tables"]["research_objects"].append(obj)
        rs["tables"]["research_object_publications"].append(publication)
        rs["tables"]["research_object_outcomes"].append(
            {"publication_id": publication["id"], "object_id": identity}
        )
        files[key] = body
        return obj

    feedback = artifact(
        "factor_generation_feedback",
        RDExecutionFeedback.model_validate(
            {
                **common,
                "upstream_feedback": "Fixture upstream accepted",
                "row_count": 3,
                "missing_count": 0,
                "declared_window": {
                    **inp.window.model_dump(mode="json"),
                    "complete": True,
                },
                "objective_passed": True,
            }
        ),
    )
    proposal = artifact(
        "factor_generation_proposal",
        RDExecutionProposal.model_validate(
            {
                **common,
                "source_assembly_id": assembly,
                "source_assembly_sha256": "d" * 64,
            }
        ),
    )
    adoption = artifact(
        "factor_adoption_receipt",
        RDExecutionAdoption.model_validate(
            {
                **common,
                "observation_id": observation,
                "boundary_id": boundary,
                "controller_launch_id": controller,
                "proposal_object_id": proposal["id"],
                "proposal_sha256": proposal["sha256"],
                "feedback_object_id": feedback["id"],
                "feedback_sha256": feedback["sha256"],
                "adopted_source_id": source,
            }
        ),
    )
    rd["tables"].update(
        {
            "factor_generation_starts": [
                {**scope, "id": start, "mutation_reservation_id": reservation}
            ],
            "factor_evaluation_starts": [
                {**scope, "id": evaluation, "evaluated_source_id": source}
            ],
            "factor_generation_sources": [
                {
                    **scope,
                    "id": source,
                    "start_id": start,
                    "factor_version_id": common["factor_version_id"],
                    "source_sha256": common["code_sha256"],
                    "assembly_id": assembly,
                }
            ],
            "factor_generation_boundaries": [
                {
                    **scope,
                    "id": boundary,
                    "start_id": start,
                    "boundary": "returned",
                    "controller_launch_id": controller,
                    "observation_id": observation,
                }
            ],
            "factor_response_assemblies": [
                {**scope, "id": assembly, "object_sha256": "d" * 64}
            ],
            "factor_generation_outcomes": [
                {
                    **scope,
                    "id": uid(),
                    "start_id": start,
                    "outcome": "accepted",
                    "logical_step": 0,
                    "error_code": None,
                    "feedback_object_id": feedback["id"],
                    "proposal_object_id": proposal["id"],
                    "adoption_object_id": adoption["id"],
                    "adoption_sha256": adoption["sha256"],
                    "adoption_object_kind": "factor_adoption_receipt",
                    **{
                        key: source
                        for key in (
                            "evaluated_source_id",
                            "adopted_source_id",
                            "continuation_source_id",
                        )
                    },
                    **{
                        key: common["factor_version_id"]
                        for key in (
                            "factor_version_id",
                            "adopted_factor_version_id",
                            "continuation_factor_version_id",
                        )
                    },
                }
            ],
        }
    )
    files["rows.json"] = {"rows": [r.model_dump(mode="json") for r in rows]}
    ex["tables"].update(
        {
            "factor_execution_evaluation_outcomes": [
                {
                    **scope,
                    "id": uid(),
                    "generation_start_id": start,
                    "evaluation_id": evaluation,
                    "snapshot_id": snapshot,
                    "boundary_id": boundary,
                    "controller_launch_id": controller,
                    "snapshot_input_sha256": common["input_sha256"],
                    "snapshot_output_sha256": common["output_sha256"],
                    "snapshot_manifest_sha256": common["manifest_sha256"],
                    "feedback_object_id": feedback["id"],
                    "feedback_sha256": feedback["sha256"],
                }
            ],
            "factor_execution_snapshot_publications": [
                {
                    **scope,
                    "id": snapshot,
                    "journal": {
                        "manifest": {
                            "input": inp.model_dump(mode="json"),
                            "assembled_rows": {
                                "storage_key": "rows.json",
                                "sha256": digest(files["rows.json"]),
                            },
                        }
                    },
                }
            ],
            "factor_execution_snapshot_outcomes": [
                {**scope, "publication_id": snapshot}
            ],
        }
    )
    return rd, ex, rs, files


def validate(graph):
    rd, ex, rs, files = graph
    return validate_execution_generation_links(
        rd, ex, rs, lambda key: copy.deepcopy(files[key]), 100
    )


def test_actual_fixture_and_typed_final_links(graph):
    assert validate(graph) is True


def test_pending_has_no_inferred_final_success(graph):
    graph[0]["tables"]["factor_generation_outcomes"] = []
    graph[1]["tables"]["factor_execution_evaluation_outcomes"] = []
    assert validate(graph) is True


@pytest.mark.parametrize("index", [0, 1, 2])
def test_exact_closure_schema(graph, index):
    graph[index]["schema_version"] = "historical-or-future"
    with pytest.raises(ValueError):
        validate(graph)


@pytest.mark.parametrize(
    "table",
    [
        "factor_generation_starts",
        "factor_evaluation_starts",
        "factor_generation_sources",
        "factor_generation_boundaries",
        "factor_generation_outcomes",
    ],
)
def test_foreign_scope(graph, table):
    graph[0]["tables"][table][0]["owner_id"] = uid()
    with pytest.raises(ValueError):
        validate(graph)


@pytest.mark.parametrize(
    "change",
    [
        "body",
        "hash",
        "kind",
        "publication_scope",
        "missing_mutation_charge",
        "wrong_charge",
        "missing_outcome",
    ],
)
def test_retained_graph_negative(graph, change):
    _rd, _ex, rs, files = graph
    obj = rs["tables"]["research_objects"][0]
    if change == "body":
        files[obj["storage_key"]]["upstream_feedback"] = "drift"
    elif change == "hash":
        obj["sha256"] = "f" * 64
    elif change == "kind":
        obj["object_kind"] = "factor_generation_proposal"
    elif change == "publication_scope":
        rs["tables"]["research_object_publications"][0]["attempt_id"] = uid()
    elif change == "missing_mutation_charge":
        rs["tables"]["research_budget_settlements"] = []
    elif change == "wrong_charge":
        rs["tables"]["research_budget_settlements"][0]["charge"] = {"mutation_slots": 0}
    else:
        rs["tables"]["research_object_outcomes"] = []
    with pytest.raises(ValueError):
        validate(graph)


@pytest.mark.parametrize(
    "key,value",
    [
        ("source_id", None),
        ("evaluation_id", None),
        ("snapshot_id", None),
        ("objective_passed", False),
    ],
)
def test_rehashed_feedback_semantic_drift(graph, key, value):
    _, ex, rs, files = graph
    obj = rs["tables"]["research_objects"][0]
    body = files[obj["storage_key"]]
    body[key] = uid() if value is None else value
    obj.update(sha256=digest(body), size_bytes=len(canonical_bytes(body)))
    ex["tables"]["factor_execution_evaluation_outcomes"][0]["feedback_sha256"] = obj[
        "sha256"
    ]
    with pytest.raises(ValueError):
        validate(graph)


@pytest.mark.parametrize(
    "index,key",
    [
        (1, "source_assembly_id"),
        (1, "source_assembly_sha256"),
        (2, "proposal_object_id"),
        (2, "feedback_object_id"),
        (2, "boundary_id"),
        (2, "observation_id"),
        (2, "controller_launch_id"),
    ],
)
def test_rehashed_proposal_adoption_reference_drift(graph, index, key):
    rd, _, rs, files = graph
    obj = rs["tables"]["research_objects"][index]
    body = files[obj["storage_key"]]
    body[key] = "f" * 64 if key.endswith("sha256") else uid()
    obj.update(sha256=digest(body), size_bytes=len(canonical_bytes(body)))
    if index == 2:
        rd["tables"]["factor_generation_outcomes"][0]["adoption_sha256"] = obj["sha256"]
    with pytest.raises(ValueError):
        validate(graph)


def test_measured_failed_objective_cannot_be_accepted(graph):
    _, ex, rs, files = graph
    files["rows.json"]["rows"][0]["value"] = 0.0
    publication = ex["tables"]["factor_execution_snapshot_publications"][0]
    publication["journal"]["manifest"]["assembled_rows"]["sha256"] = digest(
        files["rows.json"]
    )
    obj = rs["tables"]["research_objects"][0]
    body = files[obj["storage_key"]]
    body["objective_passed"] = False
    obj.update(sha256=digest(body), size_bytes=len(canonical_bytes(body)))
    ex["tables"]["factor_execution_evaluation_outcomes"][0]["feedback_sha256"] = obj[
        "sha256"
    ]
    with pytest.raises(ValueError):
        validate(graph)
    # Failure evidence itself is preservable; absence of acceptance is not success.
    graph[0]["tables"]["factor_generation_outcomes"] = []
    assert validate(graph) is True


@pytest.mark.parametrize("maximum", [True, 0, -1, 1])
def test_exact_row_bound(graph, maximum):
    rd, ex, rs, files = graph
    with pytest.raises(ValueError):
        validate_execution_generation_links(rd, ex, rs, files.__getitem__, maximum)
