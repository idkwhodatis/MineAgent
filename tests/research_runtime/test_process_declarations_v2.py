import json
from uuid import uuid4

import pytest

from packages.research_runtime.process_contracts import ProcessAdmission, ProcessError
from packages.research_runtime.process_declarations_v2 import (
    ProcessDeclarationV2,
    declaration_bytes,
    declared_ticket_sha256,
    require_v2_launch_authority,
)


def data(role="factor_cutoff_candidate"):
    return {
        "schema_version": "mineagent.process-admission.v2",
        **{
            key: str(uuid4())
            for key in (
                "id",
                "owner_id",
                "run_id",
                "attempt_id",
                "logical_operation_id",
                "reservation_id",
            )
        },
        "generation": 1,
        "launch_ordinal": 1,
        "role": role,
        "profile": "disabled.exact-profile.v2",
        "input_sha256": "a" * 64,
        "image_id": "sha256:" + "b" * 64,
        "ipc": {"ipc_policy": "none", "ticket_sha256": None},
    }


def parse(value):
    return ProcessDeclarationV2.model_validate_json(json.dumps(value))


@pytest.mark.parametrize(
    "role", ["factor_cutoff_candidate", "factor_normalizer", "native_evaluator"]
)
def test_no_ipc_role_has_explicit_null_not_a_dummy_ticket(role):
    value = parse(data(role))
    assert json.loads(declaration_bytes(value))["ipc"] == {
        "ipc_policy": "none",
        "ticket_sha256": None,
    }
    with pytest.raises(ProcessError, match="no_ipc"):
        declared_ticket_sha256(value)
    with pytest.raises(ProcessError, match="not_activated"):
        require_v2_launch_authority(value)


@pytest.mark.parametrize("role", ["rd_orchestrator", "openevolve_orchestrator"])
def test_controller_declares_ticket_but_still_has_no_authority(role):
    value = data(role)
    with pytest.raises(ValueError):
        parse(value)
    value["ipc"] = {"ipc_policy": "host_ticket", "ticket_sha256": "c" * 64}
    declared = parse(value)
    assert declared_ticket_sha256(declared) == "c" * 64
    with pytest.raises(ProcessError, match="not_activated"):
        require_v2_launch_authority(declared)


@pytest.mark.parametrize(
    "ipc",
    [
        {"ipc_policy": "host_ticket", "ticket_sha256": "c" * 64},
        {"ipc_policy": "none", "ticket_sha256": "0" * 64},
        {"ipc_policy": "none"},
        {"ipc_policy": "none", "ticket_sha256": None, "socket": "/ipc"},
        {"ipc_policy": "unknown", "ticket_sha256": None},
    ],
)
def test_candidate_cannot_inherit_or_invent_ipc(ipc):
    value = data()
    value["ipc"] = ipc
    with pytest.raises(ValueError):
        parse(value)


@pytest.mark.parametrize("number", [True, False, 0, -1, 1.0, "1", 2**31])
def test_strict_bounded_generation_and_launch(number):
    for key in ("generation", "launch_ordinal"):
        value = data()
        value[key] = number
        with pytest.raises(ValueError):
            parse(value)


def test_v1_data_never_upcasts_or_changes_serialization():
    old = ProcessAdmission(
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        1,
        "neutral_fixture",
        uuid4(),
        1,
        "neutral_fixture.v1",
        "a" * 64,
        uuid4(),
        "sha256:" + "b" * 64,
        "c" * 64,
    )
    assert old.public()["ticket_sha256"] == "c" * 64
    assert "schema_version" not in old.public() and "ipc" not in old.public()
    with pytest.raises(ValueError):
        parse(old.public())


def test_v2_object_cannot_skip_role_validation_via_model_construct():
    valid = parse(data())
    forged = ProcessDeclarationV2.model_construct(
        **{**valid.__dict__, "role": "rd_orchestrator"}
    )
    with pytest.raises(ValueError):
        declaration_bytes(forged)
