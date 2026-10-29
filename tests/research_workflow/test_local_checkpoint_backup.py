"""Actual normalized bytes joined to retained local checkpoint commitments."""

from copy import deepcopy
from uuid import uuid4

import pytest

from backend.app import research_input_backup as source
from backend.app.research_workflow_backup import validate_local_checkpoints
from packages.factors.contracts import canonical_bytes, digest
from packages.research_workflow.local_checkpoint import LocalNormalizationCheckpoint
from tests.research_inputs.test_backup import feedback_graph as feedback_graph  # noqa: PLC0414
from tests.research_inputs.test_backup import graph as graph  # noqa: PLC0414


@pytest.mark.parametrize(
    "fault", [None, "event", "event_sha", "object", "size", "missing_facts"]
)
def test_actual_source_terminal_is_required(graph, fault):
    facts = source.validate_closure(graph.value, **graph.args)
    terminal = facts[graph.row["id"]]["events"][-1]
    identity = str(uuid4())
    checkpoint = LocalNormalizationCheckpoint.model_validate_json(
        canonical_bytes(
            {
                "owner_id": graph.row["owner_id"],
                "workflow_id": graph.row["workflow_id"],
                "stage_id": graph.row["stage_id"],
                "input_revision_id": str(uuid4()),
                "input_sha256": "1" * 64,
                "definition_sha256": graph.row["source_sha256"],
                "allocation_sha256": "2" * 64,
                "derivation_id": graph.row["id"],
                "terminal_event_id": str(terminal.id),
                "terminal_event_sha256": digest(terminal),
                "state": "succeeded",
                "outputs": [ref.model_dump(mode="json") for ref in terminal.outputs],
                "objects": [
                    {k: row[k] for k in ("id", "kind", "sha256", "size_bytes")}
                    for row in (graph.raw, graph.output)
                ],
            }
        )
    )
    if fault == "event":
        checkpoint = checkpoint.model_copy(update={"terminal_event_id": uuid4()})
    elif fault == "event_sha":
        checkpoint = checkpoint.model_copy(update={"terminal_event_sha256": "f" * 64})
    elif fault in {"object", "size"}:
        obj = checkpoint.objects[1].model_copy(
            update={"id": uuid4()}
            if fault == "object"
            else {"size_bytes": checkpoint.objects[1].size_bytes + 1}
        )
        checkpoint = checkpoint.model_copy(
            update={"objects": (checkpoint.objects[0], obj)}
        )
    elif fault == "missing_facts":
        facts = {}
    metadata = {
        "indexes": {
            "research_workflow_checkpoints": {
                identity: {
                    "stage_id": graph.row["stage_id"],
                    "owner_id": graph.row["owner_id"],
                    "workflow_id": graph.row["workflow_id"],
                }
            },
            "research_workflow_stages": {
                graph.row["stage_id"]: {"kind": "external_normalization"}
            },
        },
        "checkpoints": {identity: checkpoint},
    }
    if fault is None:
        validate_local_checkpoints(
            metadata, input_closure=graph.value, input_facts=facts
        )
        assert graph.reads
    else:
        with pytest.raises(ValueError):
            validate_local_checkpoints(
                metadata, input_closure=deepcopy(graph.value), input_facts=facts
            )


@pytest.mark.parametrize("wrong_release", [False, True])
def test_feedback_checkpoint_binds_actual_source_release(feedback_graph, wrong_release):
    from packages.research_inputs.feedback import FeedbackProjectionDefinition
    from packages.research_workflow.feedback_checkpoint import FeedbackCheckpoint

    g = feedback_graph
    facts = source.validate_closure(g.value, **g.args)
    terminal = facts[g.row["id"]]["events"][-1]
    definition = FeedbackProjectionDefinition.model_validate_json(
        g.row["source_canonical"]
    )
    obj = next(
        row
        for row in g.value["tables"]["research_input_objects"]
        if row["kind"] == "normalized_feedback"
    )
    checkpoint = FeedbackCheckpoint.model_validate_json(
        canonical_bytes(
            {
                "owner_id": g.row["owner_id"],
                "workflow_id": g.row["workflow_id"],
                "stage_id": g.row["stage_id"],
                "input_revision_id": str(uuid4()),
                "input_sha256": "1" * 64,
                "definition_sha256": g.row["source_sha256"],
                "allocation_sha256": "2" * 64,
                "rd_checkpoint_id": str(uuid4()),
                "source": definition.source.model_dump(mode="json"),
                "derivation_id": g.row["id"],
                "terminal_event_id": str(terminal.id),
                "terminal_event_sha256": digest(terminal),
                "state": "succeeded",
                "outputs": [ref.model_dump(mode="json") for ref in terminal.outputs],
                "objects": [
                    {k: obj[k] for k in ("id", "kind", "sha256", "size_bytes")}
                ],
                "exposure_ids": [str(v) for v in g.feedback.proposal.exposure_ids],
            }
        )
    )
    if wrong_release:
        checkpoint = checkpoint.model_copy(update={"exposure_ids": (uuid4(), uuid4())})
    identity = str(uuid4())
    metadata = {
        "indexes": {
            "research_workflow_checkpoints": {
                identity: {
                    "stage_id": g.row["stage_id"],
                    "owner_id": g.row["owner_id"],
                    "workflow_id": g.row["workflow_id"],
                }
            },
            "research_workflow_stages": {
                g.row["stage_id"]: {"kind": "experiment_feedback_projection"}
            },
        },
        "checkpoints": {identity: checkpoint},
    }
    if wrong_release:
        with pytest.raises(ValueError, match="release ledger"):
            validate_local_checkpoints(
                metadata, input_closure=g.value, input_facts=facts
            )
    else:
        validate_local_checkpoints(metadata, input_closure=g.value, input_facts=facts)


@pytest.mark.parametrize(
    "fault", [None, "child_text", "version_hash", "missing_derivation"]
)
def test_frozen_child_text_reconstructed_from_actual_versions(graph, fault):
    from types import SimpleNamespace as NS
    from uuid import UUID

    from backend.app.research_workflow_backup import validate_resolved_inputs
    from packages.research_workflow.contracts import WorkflowInputSnapshot

    g = graph
    facts = source.validate_closure(g.value, **g.args)
    ref = facts[g.row["id"]]["events"][-1].outputs[0]
    if fault == "version_hash":
        ref = ref.model_copy(update={"content_sha256": "f" * 64})
    selection = WorkflowInputSnapshot(
        owner_id=UUID(g.row["owner_id"]),
        workflow_id=UUID(g.row["workflow_id"]),
        revision_id=uuid4(),
        refs=(ref,),
    )
    metadata = {"selections": {str(selection.revision_id): selection}}
    kwargs = {
        "input_closure": g.value,
        "input_facts": facts,
        "steer_closure": {
            "schema_version": "mineagent.rd-steer-closure.v1",
            "tables": {"rd_steer_versions": []},
        },
    }
    if fault == "missing_derivation":
        kwargs["input_facts"] = {}
    if fault in {"version_hash", "missing_derivation"}:
        with pytest.raises(ValueError):
            validate_resolved_inputs(metadata, configs={}, **kwargs)
        return
    actual = validate_resolved_inputs(metadata, configs={}, **kwargs)[
        str(selection.revision_id)
    ]
    retained = actual
    if fault == "child_text":
        retained = (
            actual[0].model_copy(update={"text": "Silently replaced guidance"}),
        )
    config = NS(workflow_inputs=NS(selection=selection, inputs=retained))
    if fault:
        with pytest.raises(ValueError, match="retained text"):
            validate_resolved_inputs(metadata, configs={"child": config}, **kwargs)
    else:
        validate_resolved_inputs(metadata, configs={"child": config}, **kwargs)
        assert actual[0].text == facts[g.row["id"]]["outputs"][0].text


@pytest.mark.parametrize("text", ["Keep\r\noriginal text", "😀" * 2048])
def test_manual_crlf_immutable_document_has_owner_in_row_not_document(text):
    from hashlib import sha256

    from backend.app.research_workflow_backup import validate_resolved_inputs
    from packages.rdagent_adapter.steer import SteerVersion
    from packages.research_inputs.contracts import InputVersionRef
    from packages.research_workflow.contracts import WorkflowInputSnapshot
    from tests.research_inputs.test_backup import empty

    owner = uuid4()
    value = SteerVersion(
        steer_id=uuid4(),
        version_id=uuid4(),
        ordinal=1,
        kind="direction",
        text=text,
        text_sha256=sha256(text.encode()).hexdigest(),
    )
    selection = WorkflowInputSnapshot(
        owner_id=owner,
        workflow_id=uuid4(),
        revision_id=uuid4(),
        refs=(
            InputVersionRef(
                origin="manual_user_submission",
                record_id=value.steer_id,
                version_id=value.version_id,
                content_sha256=value.sha256,
            ),
        ),
    )
    kwargs = {
        "configs": {},
        "input_closure": empty(),
        "input_facts": {},
        "steer_closure": {
            "schema_version": "mineagent.rd-steer-closure.v1",
            "tables": {
                "rd_steer_versions": [
                    {
                        "id": str(value.version_id),
                        "owner_id": str(owner),
                        "steer_id": str(value.steer_id),
                        "content_sha256": value.sha256,
                        "content_canonical": canonical_bytes(value).decode(),
                    }
                ]
            },
        },
    }
    metadata = {"selections": {str(selection.revision_id): selection}}
    actual = validate_resolved_inputs(metadata, **kwargs)
    assert actual[str(selection.revision_id)][0].text == value.text
    kwargs["steer_closure"]["tables"]["rd_steer_versions"][0]["owner_id"] = str(uuid4())
    with pytest.raises(ValueError, match="manual version"):
        validate_resolved_inputs(metadata, **kwargs)
