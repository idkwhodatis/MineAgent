"""Synthetic journal integrity checks, not runtime acceptance."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from packages.research_runtime.process_contracts import ProcessError, digest
from packages.research_runtime.process_declarations_v2 import (
    NoIPC,
    ProcessDeclarationV2,
)
from runtimes.structured_factor_engine.journal import (
    PROFILE,
    StructuredExtraction,
    StructuredIdentity,
    StructuredInputBinding,
    StructuredIntent,
    StructuredLaunch,
    StructuredStop,
    validate_structured_journal,
)


def records():
    binding = StructuredInputBinding(
        execution_id=uuid4(),
        snapshot_id=uuid4(),
        structured_strategy_version_id=uuid4(),
        market_sha256="a" * 64,
        market_size_bytes=20,
        strategy_input_sha256="b" * 64,
        strategy_input_size_bytes=10,
    )
    admission = ProcessDeclarationV2(
        schema_version="mineagent.process-admission.v2",
        id=uuid4(),
        owner_id=uuid4(),
        run_id=uuid4(),
        attempt_id=uuid4(),
        generation=1,
        role="native_evaluator",
        logical_operation_id=binding.execution_id,
        launch_ordinal=1,
        profile=PROFILE,
        input_sha256=digest(binding.model_dump(mode="json")),
        reservation_id=uuid4(),
        image_id="sha256:" + "c" * 64,
        ipc=NoIPC(ipc_policy="none", ticket_sha256=None),
    )
    now = datetime.now(UTC)
    intent = StructuredIntent(
        id=admission.id,
        admission=admission,
        inputs=binding,
        issued_at=now,
        not_after=now + timedelta(seconds=8),
    )
    launch = StructuredLaunch(
        intent=intent,
        runtime_name="mineagent-factor-strategy-" + str(admission.id),
        root_device=1,
        root_inode=2,
    )
    identity = StructuredIdentity(
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
    stop = StructuredStop(
        launch_sha256=identity.launch_sha256,
        identity_sha256=digest(identity.model_dump(mode="json")),
        container_id=identity.container_id,
        confirmed_absent=True,
        kernel_identity_absent=True,
        observed_at=now + timedelta(seconds=8),
        deadline_bound_met=True,
    )
    extraction = StructuredExtraction(
        launch_sha256=identity.launch_sha256,
        identity_sha256=stop.identity_sha256,
        sha256="e" * 64,
        size_bytes=100,
        strategy_input_sha256=binding.strategy_input_sha256,
        market_sha256=binding.market_sha256,
        protected_frame_complete=True,
        producer_quiesced=True,
    )
    return launch, identity, stop, extraction


def test_complete_integrity_chain():
    values = records()
    assert validate_structured_journal(*values) == values


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
        validate_structured_journal(launch, identity, stop, extraction)


def test_late_containment_not_result_authority():
    launch, identity, stop, extraction = records()
    stop = stop.model_copy(
        update={
            "deadline_bound_met": False,
            "observed_at": launch.intent.not_after + timedelta(minutes=1),
        }
    )
    validate_structured_journal(launch, identity, stop)
    with pytest.raises(ProcessError):
        validate_structured_journal(launch, identity, stop, extraction)


@pytest.mark.parametrize("field", ["market_sha256", "strategy_input_sha256"])
def test_result_input_substitution_rejected(field):
    launch, identity, stop, extraction = records()
    with pytest.raises(ProcessError):
        validate_structured_journal(
            launch, identity, stop, extraction.model_copy(update={field: "f" * 64})
        )


def test_exact_two_inputs_and_trusted_closed_qualification():
    launch, _, _, extraction = records()
    assert {item.destination for item in launch.intent.inputs.files} == {
        "/input/strategy.json",
        "/input/market.json",
    }
    assert extraction.qualification == "closed_operator_trusted_engine"
    with pytest.raises(ValueError):
        StructuredExtraction.model_validate(
            extraction.model_copy(update={"qualification": "isolated_policy"})
        )
