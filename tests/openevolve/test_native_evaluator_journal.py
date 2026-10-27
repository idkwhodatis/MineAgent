"""Synthetic journal integrity checks, not runtime acceptance."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from packages.research_runtime.process_contracts import ProcessError, digest
from packages.research_runtime.process_declarations_v2 import (
    NoIPC,
    ProcessDeclarationV2,
)
from runtimes.openevolve_evaluator.journal import (
    PROFILE,
    NativeExtraction,
    NativeIdentity,
    NativeInputBinding,
    NativeIntent,
    NativeLaunch,
    NativeStop,
    validate_native_journal,
)


def records():
    binding = NativeInputBinding(
        evaluation_id=uuid4(),
        manifest_object_id=uuid4(),
        manifest_sha256="a" * 64,
        native_artifacts_sha256="b" * 64,
        manifest_size_bytes=100,
        execution_plan_sha256="c" * 64,
        source_sha256="d" * 64,
    )
    admission = ProcessDeclarationV2(
        schema_version="mineagent.process-admission.v2",
        id=uuid4(),
        owner_id=uuid4(),
        run_id=uuid4(),
        attempt_id=uuid4(),
        generation=1,
        role="native_evaluator",
        logical_operation_id=binding.evaluation_id,
        launch_ordinal=1,
        profile=PROFILE,
        input_sha256=digest(binding.model_dump(mode="json")),
        reservation_id=uuid4(),
        image_id="sha256:" + "c" * 64,
        ipc=NoIPC(ipc_policy="none", ticket_sha256=None),
    )
    now = datetime.now(UTC)
    intent = NativeIntent(
        id=admission.id,
        admission=admission,
        inputs=binding,
        issued_at=now,
        not_after=now + timedelta(seconds=8),
    )
    launch = NativeLaunch(
        intent=intent,
        runtime_name="mineagent-oe-native-" + str(admission.id),
        root_device=1,
        root_inode=2,
    )
    identity = NativeIdentity(
        admission_sha256=digest(admission.model_dump(mode="json")),
        launch_sha256=digest(launch.model_dump(mode="json")),
        container_id="d" * 64,
        image_id=admission.image_id,
        root_device=1,
        root_inode=2,
        init_pid=123,
        init_start_ticks=999,
        boot_id=uuid4(),
        pid_namespace_inode=3,
    )
    stop = NativeStop(
        launch_sha256=identity.launch_sha256,
        identity_sha256=digest(identity.model_dump(mode="json")),
        container_id=identity.container_id,
        confirmed_absent=True,
        kernel_identity_absent=True,
        observed_at=now + timedelta(seconds=8),
        deadline_bound_met=True,
    )
    extraction = NativeExtraction(
        launch_sha256=identity.launch_sha256,
        identity_sha256=stop.identity_sha256,
        sha256="e" * 64,
        size_bytes=100,
        manifest_sha256=binding.manifest_sha256,
        native_artifacts_sha256=binding.native_artifacts_sha256,
        protected_frame_complete=True,
        producer_quiesced=True,
    )
    return launch, identity, stop, extraction


def test_complete_integrity_chain():
    values = records()
    assert validate_native_journal(*values) == values


def test_forged_matching_launch_hash_rejected():
    launch, identity, stop, extraction = records()
    identity = identity.model_copy(update={"launch_sha256": "f" * 64})
    stop = stop.model_copy(
        update={
            "launch_sha256": "f" * 64,
            "identity_sha256": digest(identity.model_dump(mode="json")),
        }
    )
    with pytest.raises(ProcessError):
        validate_native_journal(launch, identity, stop, extraction)


def test_late_containment_not_result_authority():
    launch, identity, stop, extraction = records()
    stop = stop.model_copy(
        update={
            "deadline_bound_met": False,
            "observed_at": launch.intent.not_after + timedelta(minutes=1),
        }
    )
    validate_native_journal(launch, identity, stop)
    with pytest.raises(ProcessError):
        validate_native_journal(launch, identity, stop, extraction)


@pytest.mark.parametrize("field", ["manifest_sha256", "native_artifacts_sha256"])
def test_result_input_substitution_rejected(field):
    launch, identity, stop, extraction = records()
    with pytest.raises(ProcessError):
        validate_native_journal(
            launch, identity, stop, extraction.model_copy(update={field: "f" * 64})
        )


def test_retained_manifest_binds_both_distinct_hashes():
    from packages.research_runtime.process_contracts import canonical
    from tests.openevolve.test_native_evaluator_contracts import manifest

    value, _ = manifest()
    binding = NativeInputBinding(
        evaluation_id=value.evaluation_id,
        manifest_object_id=uuid4(),
        manifest_sha256=value.sha256,
        manifest_size_bytes=len(canonical(value.model_dump(mode="json"))),
        native_artifacts_sha256=value.native_artifacts_sha256,
        execution_plan_sha256=value.execution_plan_sha256,
        source_sha256=value.source_sha256,
    )
    assert binding.require_manifest(value) == value
    assert binding.manifest_sha256 != binding.native_artifacts_sha256
    with pytest.raises(ProcessError):
        binding.model_copy(
            update={"manifest_sha256": value.native_artifacts_sha256}
        ).require_manifest(value)


@pytest.mark.parametrize("seconds", [0, 61, -1])
def test_deadline_ceiling_is_sixty_seconds(seconds):
    launch, *_ = records()
    with pytest.raises(ValueError):
        NativeIntent.model_validate(
            launch.intent.model_copy(
                update={
                    "not_after": launch.intent.issued_at + timedelta(seconds=seconds)
                }
            )
        )
