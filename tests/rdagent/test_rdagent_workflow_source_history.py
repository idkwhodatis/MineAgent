"""Pure graph and actual guarded-file checks; not PostgreSQL acceptance."""

import hashlib
import time
from copy import deepcopy

import pytest

from backend.app.factor_native_io import native_storage_guard
from backend.app.rdagent_workflow_source_history import (
    _exact,
    _members,
    _physical_reader,
)


def chain():
    source = {"call_id": "call", "start_id": "start", "context_id": "context"}
    rows = {}
    for n in range(2):
        rows[str(n)] = dict(
            source,
            id=str(n),
            request_ordinal=n,
            predecessor_id=None if n == 0 else str(n - 1),
            relation="initial" if n == 0 else "continuation",
            parse_failure_object_id=None,
            parse_failure_object_kind=None,
            parse_failure_sha256=None,
        )
    return source, rows


def test_exact_rejects_missing_and_ambiguous():
    for rows in ({}, {"a": {}, "b": {}}):
        with pytest.raises(ValueError):
            _exact(rows, lambda row: True, "not exact")


@pytest.mark.parametrize(
    "change", ["ordinal", "predecessor", "context", "repair", "parse_object", "empty"]
)
def test_source_chain_is_contiguous_and_no_unregistered_repair(change):
    source, rows = chain()
    assert len(_members(rows, source)) == 2
    rows = deepcopy(rows)
    if change == "ordinal":
        rows["1"]["request_ordinal"] = 2
    if change == "predecessor":
        rows["1"]["predecessor_id"] = "other"
    if change == "context":
        rows["1"]["context_id"] = "other"
    if change == "repair":
        rows["1"]["relation"] = "semantic_repair"
    if change == "parse_object":
        rows["1"]["parse_failure_object_id"] = "other"
    if change == "empty":
        rows = {}
    with pytest.raises(ValueError):
        _members(rows, source)


def test_actual_retained_bytes_hash_deadline_and_symlink(tmp_path):
    (tmp_path / "factors").mkdir()
    raw = b"actual retained source"
    (tmp_path / "evidence").write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()
    with native_storage_guard(tmp_path):
        read = _physical_reader(tmp_path, time.monotonic() + 10)
        assert read("evidence", sha, 1024) == raw
        with pytest.raises(ValueError):
            read("evidence", "0" * 64, 1024)
        with pytest.raises(ValueError):
            _physical_reader(tmp_path, time.monotonic() - 1)("evidence", sha, 1024)
        (tmp_path / "link").symlink_to(tmp_path / "evidence")
        with pytest.raises(OSError):
            read("link", sha, 1024)


@pytest.mark.parametrize(
    "failure", ["live_run", "missing_attempt", "unfinished", "unstopped"]
)
def test_actual_final_run_and_attempt_required_before_any_evidence_read(
    tmp_path, failure
):
    from types import SimpleNamespace as NS
    from uuid import uuid4

    from backend.app.rdagent_workflow_source_history import (
        verify_workflow_source_history,
    )
    from packages.rdagent_adapter.persisted_identity import LEGACY_ONLY_FIELDS
    from tests.rdagent.test_rdagent_workflow_child_contracts import config

    value = config()
    actual = NS(**{field: None for field in LEGACY_ONLY_FIELDS})
    actual.__dict__.update(
        id=value.workflow_child_run_id,
        owner_id=value.workflow_inputs.binding.owner_id,
        purpose="factor_research",
        engine="rdagent",
        config=value.model_dump(mode="json"),
        dataset_version_id=value.input.dataset.dataset_version_id,
        request_sha256=value.sha256,
        research_image_digest=value.runtime.orchestrator_image,
        status="running" if failure == "live_run" else "succeeded",
        active_attempt_id=uuid4(),
        generation=1,
    )
    attempt = NS(
        finished_at=None if failure == "unfinished" else "finished",
        sandbox_stopped=failure != "unstopped",
    )
    rows = iter([actual, None if failure == "missing_attempt" else attempt])
    session = NS(
        scalars=lambda query: NS(all=lambda: ["029_research_workflow"]),
        scalar=lambda query: next(rows),
    )
    (tmp_path / "factors").mkdir()
    with native_storage_guard(tmp_path), pytest.raises(ValueError, match="Actual"):
        verify_workflow_source_history(
            session, actual, NS(id=uuid4()), NS(storage_root=tmp_path)
        )


@pytest.mark.parametrize(
    "change",
    [
        None,
        "owner_id",
        "run_id",
        "attempt_id",
        "generation",
        "workflow_id",
        "stage_id",
        "input_revision_id",
        "input_sha256",
        "operation_id",
        "transport_id",
        "request_sha256",
        "ordinal",
        "messages",
        "bytes",
    ],
)
@pytest.mark.parametrize("layout", [None, "generation_user.v1"])
def test_first_guidance_is_exact_actual_inclusion_not_compliance(change, layout):
    from uuid import uuid4

    from backend.app.rdagent_workflow_source_history import _guidance
    from packages.rdagent_adapter.workflow_prompt import first_workflow_messages
    from packages.rdagent_provider import codec
    from packages.rdagent_provider.contracts import RDProviderChatRequest
    from tests.rdagent.test_rdagent_workflow_child_contracts import config

    value = config()
    if layout is not None:
        value = type(value).model_validate(
            {**value.model_dump(mode="json"), "workflow_prompt_layout": layout}
        )
    binding = value.workflow_inputs.binding
    system, user = first_workflow_messages(value)
    request = RDProviderChatRequest(
        model_profile_id=value.model_binding.profile_id,
        model_binding_sha256=value.model_binding.sha256,
        model=value.model_binding.chat_model,
        messages=(
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ),
        max_output_tokens=value.model_binding.max_output_tokens,
        response_format={"type": "json_object"},
    )
    if change == "messages":
        request = request.model_copy(update={"messages": request.messages[:1]})
    raw = codec.canonical_bytes(request.schema_version, request)
    source = {
        "owner_id": str(binding.owner_id),
        "run_id": str(value.workflow_child_run_id),
        "attempt_id": str(uuid4()),
        "generation": 1,
    }
    call = {"id": "call", "phase": "generation", "kind": "chat", "ordinal": 0}
    member = {
        "call_id": "call",
        "request_ordinal": 0,
        "relation": "initial",
        "operation_id": "operation",
    }
    operation = {
        "id": "operation",
        "protocol": request.schema_version,
        "request": request.model_dump(mode="json"),
        "request_sha256": hashlib.sha256(raw).hexdigest(),
    }
    transport = {"id": "transport", "operation_id": "operation", "ordinal": 1}
    consumption = dict(
        source,
        workflow_id=str(binding.workflow_id),
        stage_id=str(binding.stage_id),
        input_revision_id=str(binding.input_revision_id),
        input_sha256=value.workflow_inputs_sha256,
        operation_id="operation",
        transport_id="transport",
        request_sha256=operation["request_sha256"],
    )
    if change in consumption:
        consumption[change] = 2 if change == "generation" else "different"
    elif change == "ordinal":
        transport["ordinal"] = 2
    elif change == "bytes":
        raw += b" "
    args = (value, source, call, member, operation, transport, consumption, raw, None)
    if change is None:
        assert _guidance(*args) is None
    else:
        with pytest.raises(ValueError):
            _guidance(*args)
