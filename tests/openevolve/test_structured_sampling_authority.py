"""Current controller cursor read qualification, no process/PG authority claim."""

import hashlib
from types import SimpleNamespace as NS
from uuid import UUID, uuid4

import pytest

from backend.app import oe_structured_candidate_services as service
from backend.app.oe_structured_models import OEStructuredCandidate
from backend.app.oe_structured_operational_models import OEStructuredControlState
from packages.openevolve_structured.candidate import project_candidate
from packages.openevolve_structured.checkpoint import (
    canonical,
    state_sha256,
    validate_directory,
)
from packages.research_runtime.process_contracts import ProcessError
from runtimes.openevolve_structured_host.peer import ControllerBinding
from runtimes.rdagent_operational.peer import KernelIdentity
from tests.openevolve.test_structured_checkpoint import domain, native  # noqa: F401


@pytest.fixture
def case(monkeypatch, native, domain):  # noqa: F811
    state = validate_directory(native, domain)
    scope = {
        "owner_id": uuid4(),
        "run_id": uuid4(),
        "attempt_id": uuid4(),
        "generation": 1,
    }
    fence = NS(**scope)
    epoch = uuid4()
    pid, program = next(iter(state["programs"].items()))
    projected = project_candidate(program["code"].encode(), domain)
    candidate = NS(
        **scope,
        id=uuid4(),
        epoch_id=epoch,
        upstream_program_id=UUID(pid),
        ordinal=0,
        candidate_canonical=program["code"],
        candidate_sha256=projected.candidate_sha256,
        program_version_id=uuid4(),
        provider_exchange_id=None,
        parent_candidate_id=None,
    )
    control = NS(
        **scope,
        id=uuid4(),
        epoch_id=epoch,
        phase="seed",
        sequence=1,
        frontier=0,
        state_canonical=canonical(state).decode(),
        state_sha256=state_sha256(state),
    )
    leader = KernelIdentity(100, 10, "pid:[42]", "0::/x", 1, (0,) * 4, (0,) * 4)
    child = KernelIdentity(
        101, 11, "pid:[42]", "0::/x", 100, (65533,) * 4, (65533,) * 4
    )
    peer = NS(binding=ControllerBinding("a" * 64, "b" * 64, leader, child))
    search = NS(
        domain=domain,
        maximum_mutation_slots=2,
        seed_candidate_sha256=projected.candidate_sha256,
    )
    context = NS(config=NS(search=search), bootstrap=NS(spec=search))
    rows = {
        (OEStructuredControlState, control.id): control,
        (OEStructuredCandidate, candidate.id): candidate,
    }
    latest = [control, control]
    calls = []
    session = NS(
        get=lambda cls, key: rows.get((cls, key)), scalar=lambda stmt: latest.pop(0)
    )
    monkeypatch.setattr(
        service, "require_neutral_transaction", lambda *a, **kw: (NS(), None)
    )
    monkeypatch.setattr(service, "require_native_storage_guard", lambda *a: None)
    monkeypatch.setattr(
        service,
        "require_controller_context",
        lambda *a, **kw: calls.append(kw) or context,
    )
    monkeypatch.setattr(service, "decode_candidate", lambda *a: projected)
    return NS(
        session=session,
        fence=fence,
        candidate=candidate,
        control=control,
        peer=peer,
        settings=NS(storage_root="/unused"),
        latest=latest,
        calls=calls,
        context=context,
    )


def read(case):
    return service.require_sampling_candidate(
        case.session,
        case.fence,
        case.candidate.id,
        case.settings,
        peer=case.peer,
        current_control_id=case.control.id,
    )


def test_controller_can_read_prior_member_without_synthetic_worker_intent(case):
    result = read(case)
    assert result.candidate_id == case.candidate.id
    assert all(
        call.get("intent_id") is None and call["peer"] is case.peer
        for call in case.calls
    )


@pytest.mark.parametrize(
    "change",
    [
        "worker",
        "stale",
        "sampling",
        "scope",
        "not_member",
        "hash",
        "after_cursor",
        "finished",
    ],
)
def test_prior_population_read_cannot_become_stale_or_launch_permission(case, change):
    if change == "worker":
        case.peer.binding = object()
    elif change == "stale":
        case.latest[0] = NS(id=uuid4())
    elif change == "sampling":
        case.control.phase = "sampling"
    elif change == "scope":
        case.candidate.owner_id = uuid4()
    elif change == "not_member":
        case.candidate.upstream_program_id = uuid4()
    elif change == "hash":
        case.control.state_sha256 = hashlib.sha256(b"wrong").hexdigest()
    elif change == "after_cursor":
        case.latest[1] = NS(id=uuid4())
    elif change == "finished":
        case.control.frontier = 2
    with pytest.raises(ProcessError):
        read(case)
