"""Two actual protected native units; NOT persisted OE/search/financial authority.

Fixed maintained strategy and staged market files only. Tests require real
Docker and measured parent/child image IDs, never dependency/provider calls.
"""

import base64
import json
import multiprocessing
import os
import re
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from packages.research_runtime.process_contracts import canonical, digest
from packages.research_runtime.process_declarations_v2 import (
    NoIPC,
    ProcessDeclarationV2,
)
from packages.tradegrad_adapter.independent_results import (
    RESULT_PREFIX_V3,
    validate_envelope,
)
from runtimes.openevolve_evaluator.journal import NativeInputBinding, NativeIntent
from runtimes.openevolve_evaluator.output_reader import (
    read_native_journal,
    read_stopped_native_bytes,
)
from runtimes.research_supervisor.openevolve_native_supervisor import (
    NativeSupervisor,
    _mint_native_authority,
)
from tests.integration.openevolve_native_fixture import stage_maintained_native


def setup_unit(tmp_path):
    if os.environ.get("MINEAGENT_OE_NATIVE_REQUIRED") != "1":
        pytest.skip("Explicit actual protected native evaluator gate required")
    image = os.environ.get("MINEAGENT_OE_NATIVE_IMAGE", "")
    parent = os.environ.get("MINEAGENT_OE_NATIVE_PARENT_IMAGE", "")
    assert all(re.fullmatch(r"sha256:[0-9a-f]{64}", v) for v in (image, parent))
    supervisor = NativeSupervisor(image, parent)
    inventory = supervisor.verified_inventory("native")
    evaluation = uuid4()
    _plan, manifest, files = stage_maintained_native(
        tmp_path / "stage", evaluation_id=evaluation, evaluator_image=image
    )
    binding = NativeInputBinding(
        evaluation_id=evaluation,
        manifest_object_id=uuid4(),
        manifest_sha256=manifest.sha256,
        manifest_size_bytes=len(canonical(manifest.model_dump(mode="json"))),
        native_artifacts_sha256=manifest.native_artifacts_sha256,
        execution_plan_sha256=manifest.execution_plan_sha256,
        source_sha256=manifest.source_sha256,
    )
    admission = ProcessDeclarationV2(
        schema_version="mineagent.process-admission.v2",
        id=uuid4(),
        owner_id=uuid4(),
        run_id=uuid4(),
        attempt_id=uuid4(),
        generation=1,
        role="native_evaluator",
        logical_operation_id=evaluation,
        launch_ordinal=1,
        profile="mineagent.openevolve-native-evaluator.v1",
        input_sha256=digest(binding.model_dump(mode="json")),
        reservation_id=uuid4(),
        image_id=image,
        ipc=NoIPC(ipc_policy="none", ticket_sha256=None),
    )
    now = datetime.now(UTC)
    intent = NativeIntent(
        id=admission.id,
        admission=admission,
        inputs=binding,
        issued_at=now,
        not_after=now + timedelta(seconds=60),
    )
    root = tmp_path / "process"
    root.mkdir(mode=0o700)
    return supervisor, inventory, intent, manifest, files, root


def test_actual_native_observation_protected_bytes_and_exact_stop(tmp_path):
    supervisor, inventory, intent, manifest, files, root = setup_unit(tmp_path)
    authority = _mint_native_authority(intent, manifest, files, inventory)
    identities = []
    result = supervisor.execute(root, authority, on_identity=identities.append)
    value = read_stopped_native_bytes(root, intent.admission)
    assert len(identities) == 1 and value.identity == identities[0]
    assert value.extraction == result and value.stop.deadline_bound_met is True
    assert value.stop.observed_at <= intent.not_after + timedelta(seconds=1)
    lines = value.files["stdout.bin"].decode("utf-8").splitlines()
    records = [
        line[len(RESULT_PREFIX_V3) :]
        for line in lines
        if line.startswith(RESULT_PREFIX_V3)
    ]
    assert len(records) == 1
    envelope = validate_envelope(base64.b64decode(records[0], validate=True))
    assert json.loads(value.files["metrics.json"]) == envelope["metrics"]
    identity = envelope["bundle"]["identity"]
    assert identity["strategy_sha256"] == manifest.source_sha256
    assert identity["input_manifest_sha256"] == manifest.native_artifacts_sha256
    assert identity["run_config_sha256"] == manifest.execution_plan_sha256
    assert envelope["bundle"]["window"]["start"] == manifest.window_start.isoformat()
    assert envelope["bundle"]["window"]["end"] == manifest.window_end.isoformat()
    assert value.extraction.qualification == "native_observation_not_producer_proof"
    supervisor.client.deadline = (
        None  # read-only post-proof inspection, not a new result budget
    )
    assert supervisor.client._inspect(value.identity.container_id) is None
    assert read_stopped_native_bytes(root, intent.admission) == value


def _owner(root, intent, manifest, files, inventory, image, parent, pipe):
    supervisor = NativeSupervisor(image, parent)
    authority = _mint_native_authority(intent, manifest, files, inventory)

    def captured(identity):
        pipe.send((identity.container_id, authority.deadline))

    supervisor.execute(root, authority, on_identity=captured)


def test_actual_native_owner_loss_original_deadline_without_result(tmp_path):
    supervisor, inventory, intent, manifest, files, root = setup_unit(tmp_path)
    context = multiprocessing.get_context("fork")
    receive, send = context.Pipe(duplex=False)
    owner = context.Process(
        target=_owner,
        args=(
            root,
            intent,
            manifest,
            files,
            inventory,
            supervisor.image_id,
            supervisor.installed_parent_image_id,
            send,
        ),
    )
    owner.start()
    send.close()
    cid = None
    try:
        assert receive.poll(30), "Actual identity was not observed"
        cid, deadline = receive.recv()
        owner.kill()
        owner.join(timeout=5)
        assert owner.exitcode == -9
        # No replacement host writes a stop or sends a signal before autonomous exit.
        supervisor.client.deadline = deadline + 1
        while True:
            state = supervisor.client._inspect(cid)
            assert state is not None
            if state["State"]["Running"] is False:
                assert state["State"]["ExitCode"] == 124
                break
            assert time.monotonic() < deadline + 1
            time.sleep(0.03)
        stop = supervisor.stop_and_verify(
            root, intent.admission, original_deadline=deadline + 1
        )
        assert stop.deadline_bound_met is True
        assert stop.observed_at <= intent.not_after + timedelta(seconds=1)
        assert read_native_journal(root, intent.admission)[2] == stop
        with pytest.raises(FileNotFoundError):
            read_stopped_native_bytes(root, intent.admission)
        supervisor.client.deadline = None  # receipt already proves the original budget
        assert supervisor.client._inspect(cid) is None
    finally:
        if owner.is_alive():
            owner.kill()
            owner.join(timeout=5)
        receive.close()
        # Exact saved launch identity only; no label-wide cleanup or invented stop.
        if cid is not None:
            supervisor.stop_and_verify(root, intent.admission)
