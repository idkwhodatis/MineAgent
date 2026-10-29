"""Actual request builder/bytes and cold joins; not physical container evidence."""

from uuid import uuid4

import pytest

from backend.app.workflow_oe_backup import validate_consumption
from packages.factors.contracts import canonical_bytes
from tests.workflow_oe.test_prompt_consumption import arranged


def strings(value):
    from uuid import UUID

    return {k: str(v) if isinstance(v, UUID) else v for k, v in value.items()}


@pytest.fixture
def retained():
    value = arranged()
    config, request = value.config, value.request
    context = config.workflow_context
    scope = strings(vars(value.fence))
    obj_id = str(uuid4())
    operation = strings(vars(value.operation))
    operation.update(
        protocol="mineagent.oe-structured-provider-chat.v1", request_object_id=obj_id
    )
    transport = strings(vars(value.transport))
    member = strings(vars(value.member))
    row = dict(
        id=str(uuid4()),
        **scope,
        workflow_id=str(context.inputs.binding.workflow_id),
        stage_id=str(context.inputs.binding.stage_id),
        input_revision_id=str(context.inputs.binding.input_revision_id),
        input_sha256=context.inputs.sha256,
        operation_id=operation["id"],
        transport_id=transport["id"],
        request_sha256=request.sha256,
    )
    obj = {
        "id": obj_id,
        "owner_id": scope["owner_id"],
        "storage_key": "retained/request.json",
        "sha256": request.sha256,
        "size_bytes": len(canonical_bytes(request)),
    }
    blobs = {obj["storage_key"]: canonical_bytes(request)}
    metadata = {"indexes": {"research_workflow_model_consumption": {row["id"]: row}}}
    research = {
        "tables": {
            "research_model_operations": [operation],
            "research_model_transports": [transport],
            "research_objects": [obj],
        }
    }
    oe = {"tables": {"oe_structured_provider_transports": [member]}}

    def validate():
        return validate_consumption(
            metadata,
            {scope["run_id"]: config},
            research,
            oe,
            lambda key, sha, cap: blobs[key],
        )

    return {
        "validate": validate,
        "metadata": metadata,
        "research": research,
        "oe": oe,
        "blobs": blobs,
        "row": row,
        "obj": obj,
        "member": member,
        "transport": transport,
        "operation": operation,
        "config": config,
    }


def test_actual_built_request_bytes_have_exact_consumption(retained):
    retained["validate"]()


@pytest.mark.parametrize(
    "fault",
    [
        "missing_consumption",
        "extra_consumption",
        "missing_member",
        "member_scope",
        "transport_ordinal",
        "transport_operation",
        "operation_protocol",
        "selected_not_resolved_hash",
        "wrong_stage",
        "wrong_request_hash",
        "wrong_bytes",
        "wrong_size",
    ],
)
def test_cold_consumption_requires_complete_binding_and_actual_bytes(retained, fault):
    r = retained
    if fault == "missing_consumption":
        r["metadata"]["indexes"]["research_workflow_model_consumption"].clear()
    elif fault == "extra_consumption":
        r["metadata"]["indexes"]["research_workflow_model_consumption"][
            str(uuid4())
        ] = dict(r["row"], id=str(uuid4()))
    elif fault == "missing_member":
        r["oe"]["tables"]["oe_structured_provider_transports"].clear()
    elif fault == "member_scope":
        r["member"]["attempt_id"] = str(uuid4())
    elif fault == "transport_ordinal":
        r["transport"]["ordinal"] = 2
    elif fault == "transport_operation":
        r["transport"]["operation_id"] = str(uuid4())
    elif fault == "operation_protocol":
        r["operation"]["protocol"] = "mineagent.neutral-model.v1"
    elif fault == "selected_not_resolved_hash":
        r["row"]["input_sha256"] = r["config"].workflow_context.inputs.selection.sha256
    elif fault == "wrong_stage":
        r["row"]["stage_id"] = str(uuid4())
    elif fault == "wrong_request_hash":
        r["row"]["request_sha256"] = "f" * 64
    elif fault == "wrong_bytes":
        r["blobs"][r["obj"]["storage_key"]] += b" "
    elif fault == "wrong_size":
        r["obj"]["size_bytes"] += 1
    with pytest.raises(ValueError):
        r["validate"]()


def test_rehashed_request_cannot_remove_selected_guidance(retained):
    from packages.openevolve_structured.provider_contracts import OEChatRequest

    r = retained
    raw = r["operation"]["request"].copy()
    raw["messages"] = [dict(m) for m in raw["messages"]]
    raw["messages"][1]["content"] = "No selected evidence"
    request = OEChatRequest.model_validate_json(canonical_bytes(raw))
    r["operation"]["request"] = request.model_dump(mode="json")
    for row in (r["operation"], r["member"], r["row"]):
        row["request_sha256"] = request.sha256
    r["obj"].update(sha256=request.sha256, size_bytes=len(canonical_bytes(request)))
    r["blobs"][r["obj"]["storage_key"]] = canonical_bytes(request)
    with pytest.raises(ValueError):
        r["validate"]()


def test_partition_preserves_exact_union_without_mutating_original():
    from backend.app.workflow_oe_backup import partition_rd_consumption

    rows = {"r": {"run_id": "rd"}, "o": {"run_id": "oe"}}
    metadata = {"indexes": {"research_workflow_model_consumption": rows, "other": {}}}
    result = partition_rd_consumption(metadata, {"rd": object()}, {"oe": object()})
    assert result["indexes"]["research_workflow_model_consumption"] == {"r": rows["r"]}
    assert result["indexes"]["research_workflow_model_consumption"]["r"] is rows["r"]
    assert (
        metadata["indexes"]["research_workflow_model_consumption"] is rows
        and len(rows) == 2
    )
    assert result["indexes"]["other"] is metadata["indexes"]["other"]


@pytest.mark.parametrize(
    "rd,oe,run",
    [
        ({"same": None}, {"same": None}, "same"),
        ({"rd": None}, {"oe": None}, "unknown"),
        ({}, {}, "unknown"),
    ],
)
def test_partition_never_hides_unknown_or_ambiguous_consumption(rd, oe, run):
    from backend.app.workflow_oe_backup import partition_rd_consumption

    metadata = {
        "indexes": {"research_workflow_model_consumption": {"row": {"run_id": run}}}
    }
    with pytest.raises(ValueError):
        partition_rd_consumption(metadata, rd, oe)


@pytest.mark.parametrize("generation", [True, 1.0, "1", 0])
def test_consumption_generation_is_not_coercible(retained, generation):
    retained["row"]["generation"] = generation
    with pytest.raises(ValueError):
        retained["validate"]()
