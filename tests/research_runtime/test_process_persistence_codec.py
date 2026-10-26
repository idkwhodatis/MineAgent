from uuid import uuid4

import pytest

from backend.app.process_admission_ddl_draft import (
    GRAMMAR,
    PROFILE_GATE,
    SQL,
    migration_fragment,
)
from packages.research_runtime.process_contracts import (
    ProcessAdmission,
    ProcessError,
    canonical,
)
from packages.research_runtime.process_persistence_codec import (
    V1,
    V2,
    commitment_bytes,
    declared_ipc_digest,
    decode_row,
    encode_row,
    require_persistable,
)


def row(version=V1, role="neutral_fixture"):
    return {
        **{
            key: uuid4()
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
        "profile": "neutral_fixture.v1" if version == V1 else "disabled.draft.v2",
        "input_sha256": "a" * 64,
        "image_id": "sha256:" + "b" * 64,
        "ticket_sha256": "c" * 64,
        "schema_version": version,
        "ipc_policy": "host_ticket",
    }


def test_v1_bytes_are_exact_old_public_commitment():
    original = row()
    old = ProcessAdmission(
        **{
            k: v
            for k, v in original.items()
            if k not in {"schema_version", "ipc_policy"}
        }
    )
    assert commitment_bytes(original) == canonical(old.public())
    assert encode_row(decode_row(original)) == original
    assert require_persistable(original) == old
    assert declared_ipc_digest(original) == "c" * 64


@pytest.mark.parametrize(
    "role", ["factor_cutoff_candidate", "factor_normalizer", "native_evaluator"]
)
def test_v2_no_ipc_roundtrip_has_no_authority(role):
    value = row(V2, role)
    value.update(ipc_policy="none", ticket_sha256=None)
    assert encode_row(decode_row(value)) == value
    assert b'"ticket_sha256":null' in commitment_bytes(value)
    with pytest.raises(ProcessError, match="no_ipc"):
        declared_ipc_digest(value)
    with pytest.raises(ProcessError, match="not_registered"):
        require_persistable(value)


@pytest.mark.parametrize("role", ["rd_orchestrator", "openevolve_orchestrator"])
def test_v2_controller_digest_is_not_a_profile_grant(role):
    value = row(V2, role)
    assert declared_ipc_digest(value) == "c" * 64
    with pytest.raises(ProcessError, match="not_registered"):
        require_persistable(value)


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": None},
        {"schema_version": "v2"},
        {"ipc_policy": None},
        {"ticket_sha256": None},
        {"profile": "arbitrary.v1"},
        {"role": "native_evaluator"},
        {"generation": True},
        {"id": str(uuid4())},
        {"extra": True},
    ],
)
def test_malformed_or_widened_v1_fails(change):
    value = row()
    value.update(change)
    with pytest.raises((ValueError, ProcessError)):
        decode_row(value)


@pytest.mark.parametrize(
    "change",
    [
        {"ipc_policy": "none"},
        {"ticket_sha256": None},
        {"generation": True},
        {"launch_ordinal": 2**31},
        {"profile": "x"},
        {"role": "neutral_fixture"},
    ],
)
def test_malformed_v2_fails(change):
    value = row(V2, "rd_orchestrator")
    value.update(change)
    with pytest.raises((ValueError, ProcessError)):
        decode_row(value)


def test_explicit_version_required_not_inferred():
    value = row()
    value.pop("schema_version")
    with pytest.raises(ProcessError, match="shape"):
        decode_row(value)


def test_offline_fragment_is_closed_and_null_safe():
    draft = migration_fragment()
    assert draft["activation_ready"] is False
    assert draft["registered_v2_profiles"] == ()
    assert GRAMMAR.endswith("IS TRUE") and PROFILE_GATE.endswith("IS TRUE")
    assert "ck_process_v2_unregistered" in SQL
    assert "ALTER COLUMN schema_version DROP DEFAULT" in SQL
    assert "ALTER COLUMN ipc_policy DROP DEFAULT" in SQL
    assert "UPDATE " not in SQL and "DELETE " not in SQL
    assert "LOCK TABLE research_process_admissions IN ACCESS EXCLUSIVE MODE" in SQL
