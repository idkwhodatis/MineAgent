"""Issuer structural checks only; no Docker/PG results are fabricated."""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from packages.rdagent_adapter.numeric_profile import normalizer_inputs
from packages.research_runtime.numeric_process_journal import (
    PROFILES,
    NumericInput,
    NumericInputFile,
    NumericIntent,
)
from packages.research_runtime.process_contracts import ProcessError, digest
from packages.research_runtime.process_declarations_v2 import (
    NoIPC,
    ProcessDeclarationV2,
)
from runtimes.factor_execution.acceptance import frozen_inputs
from runtimes.research_supervisor.numeric_supervisor import (
    NumericAuthority,
    NumericSupervisor,
    _mint_numeric_authority,
    check_input_bytes,
)
from workers.research.numeric_process import run_registered_numeric_unit


def bound(role="factor_cutoff_candidate"):
    candidate, normalizer = "sha256:" + "a" * 64, "sha256:" + "b" * 64
    prefix, inputs, _ = frozen_inputs(candidate, normalizer)
    source_sha = hashlib.sha256(inputs["/input/source.py"]).hexdigest()
    if role == "factor_normalizer":
        hdf = b"opaque fixture bytes; never parsed"
        _, inputs, _ = normalizer_inputs(
            prefix,
            hdf=hdf,
            expected_hdf_sha256=hashlib.sha256(hdf).hexdigest(),
            listings=inputs["/input/listings.json"],
        )
    binding = NumericInput(
        schema_version="mineagent.numeric-process-input.v2",
        source_object_id=uuid4(),
        source_sha256=source_sha,
        prefix_sha256=prefix.sha256,
        cutoff=prefix.host_cutoff,
        files=tuple(
            NumericInputFile(
                destination=k, sha256=hashlib.sha256(v).hexdigest(), size_bytes=len(v)
            )
            for k, v in sorted(inputs.items())
        ),
    )
    admission = ProcessDeclarationV2(
        schema_version="mineagent.process-admission.v2",
        **{
            key: uuid4()
            for key in (
                "id",
                "owner_id",
                "run_id",
                "attempt_id",
                "reservation_id",
                "logical_operation_id",
            )
        },
        generation=1,
        launch_ordinal=1,
        role=role,
        profile=PROFILES[role],
        input_sha256=digest(binding.model_dump(mode="json")),
        image_id=candidate if role == "factor_cutoff_candidate" else normalizer,
        ipc=NoIPC(ipc_policy="none", ticket_sha256=None),
    )
    now = datetime.now(UTC)
    return NumericIntent(
        schema_version="mineagent.numeric-process-intent.v2",
        id=uuid4(),
        admission=admission,
        inputs=binding,
        issued_at=now,
        not_after=now + timedelta(seconds=8),
    ), dict(inputs)


@pytest.mark.parametrize("role", ["factor_cutoff_candidate", "factor_normalizer"])
def test_actual_role_input_metadata_is_representable_without_launch(role):
    intent, inputs = bound(role)
    check_input_bytes(intent, inputs)
    with pytest.raises(ProcessError, match="authority"):
        NumericAuthority(object(), intent, inputs, {})


def test_private_token_is_single_use_and_bounded():
    intent, inputs = bound()
    token = _mint_numeric_authority(
        intent, inputs, {"image_id": intent.admission.image_id}
    )
    assert token.consume()["image_id"] == intent.admission.image_id
    with pytest.raises(ProcessError, match="authority"):
        token.consume()
    with pytest.raises(TypeError):
        token.inputs["/input/source.py"] = b"replacement"


@pytest.mark.parametrize(
    "mutation", ["source", "missing", "extra", "manifest", "nonbytes"]
)
def test_mounted_byte_substitution_rejected_before_any_docker(mutation):
    intent, inputs = bound()
    if mutation == "source":
        inputs["/input/source.py"] += b"\n"
    elif mutation == "manifest":
        inputs["/input/manifest.json"] = b"{}"
    elif mutation == "missing":
        del inputs["/input/source.py"]
    elif mutation == "extra":
        inputs["/input/ticket.json"] = b"secret"
    else:
        inputs["/input/source.py"] = bytearray(inputs["/input/source.py"])
    with pytest.raises(ProcessError, match="input"):
        check_input_bytes(intent, inputs)


def test_default_catalog_and_detached_declaration_never_launch(tmp_path, monkeypatch):
    intent, _ = bound()
    with pytest.raises(ProcessError, match="catalog_not_registered"):
        run_registered_numeric_unit(intent)
    supervisor = NumericSupervisor(intent.admission.image_id)
    monkeypatch.setattr(
        supervisor.client,
        "_command",
        lambda *_args, **_kwargs: pytest.fail("Docker must not be called"),
    )
    with pytest.raises(ProcessError, match="authority"):
        supervisor.execute(tmp_path, intent, on_identity=lambda _: None)
    assert list(tmp_path.iterdir()) == []


def test_expired_intent_cannot_be_reissued():
    intent, inputs = bound()
    past = datetime.now(UTC) - timedelta(seconds=20)
    intent = intent.model_copy(
        update={"issued_at": past, "not_after": past + timedelta(seconds=8)}
    )
    with pytest.raises(ProcessError, match="deadline"):
        _mint_numeric_authority(intent, inputs, {})


def test_expired_command_budget_never_spawns_docker(monkeypatch):
    import time

    import runtimes.research_supervisor.numeric_supervisor as module

    client = module.NumericDockerClient()
    client.deadline = time.monotonic() - 1
    monkeypatch.setattr(
        module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("expired command must not spawn"),
    )
    with pytest.raises(ProcessError, match="command_deadline"):
        client._command(["create", "not-an-authorized-unit"])


def test_command_uses_original_budget_not_fresh_per_command(monkeypatch):
    import subprocess
    import sys
    import time

    import runtimes.research_supervisor.numeric_supervisor as module

    original = subprocess.Popen
    # Harmless bounded local subprocess tests transport timeout only, not Docker.
    monkeypatch.setattr(
        module.subprocess,
        "Popen",
        lambda _args, **kwargs: original(
            [sys.executable, "-c", "import time; time.sleep(2)"], **kwargs
        ),
    )
    client = module.NumericDockerClient()
    client.deadline = time.monotonic() + 0.05
    before = time.monotonic()
    with pytest.raises(ProcessError, match="command_deadline"):
        client._command(["inspect", "structural-test-only"])
    assert time.monotonic() - before < 1.5


def test_hosted_composed_gate_requires_real_pg_docker_and_no_skips():
    from pathlib import Path

    workflow = (
        Path(__file__).resolve().parents[2]
        / ".github/workflows/mineagent-numeric-runtime.yml"
    ).read_text()
    for required in (
        "postgres:18.6",
        'MINEAGENT_PERSISTED_NUMERIC_REQUIRED: "1"',
        "tests/integration/test_numeric_persisted_runtime.py",
        "test_actual_source_candidate_native_claim_normalizer_stops",
        "test_actual_lost_fence_keeps_containment_without_output_import",
        "('failure','error','skipped')",
        "summarize(source,Path('var/numeric-proof/persisted-outcomes.xml'))",
    ):
        assert required in workflow
    assert "path: var/numeric-proof/" in workflow
    assert "path: var/numeric-private/" not in workflow
