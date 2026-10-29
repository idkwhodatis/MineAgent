"""Strict actual descriptor shape and persisted-placement checks with DB doubles."""

import hashlib
from dataclasses import asdict
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import oe_structured_worker_descriptor as service
from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured.host_events import WorkerDescriptor
from packages.research_runtime.process_contracts import ProcessError
from runtimes.openevolve_structured_host.peer import ControllerBinding, WorkerBinding
from runtimes.rdagent_operational.peer import KernelIdentity


def fixture(monkeypatch):
    scope = {
        "owner_id": uuid4(),
        "run_id": uuid4(),
        "attempt_id": uuid4(),
        "generation": 1,
    }
    epoch, admission = uuid4(), uuid4()
    parent = NS(
        **scope, id=uuid4(), epoch_id=epoch, ordinal=0, candidate_sha256="a" * 64
    )
    sampling = NS(
        **scope,
        id=uuid4(),
        epoch_id=epoch,
        ordinal=1,
        sampling_state_id=uuid4(),
        parent_candidate_id=parent.id,
        selection_sha256="b" * 64,
        worker_ticket_sha256=hashlib.sha256(b"c" * 64).hexdigest(),
    )
    leader = KernelIdentity(100, 10, "pid:[42]", "0::/x", 1, (0,) * 4, (0,) * 4)
    controller = KernelIdentity(
        101, 11, "pid:[42]", "0::/x", 100, (65533,) * 4, (65533,) * 4
    )
    worker = KernelIdentity(
        102, 12, "pid:[42]", "0::/x", 101, (65533,) * 4, (65533,) * 4
    )
    binding = ControllerBinding("d" * 64, "e" * 64, leader, controller)
    peer = NS(
        binding=WorkerBinding(
            binding, epoch, sampling.id, sampling.worker_ticket_sha256, worker
        )
    )
    placement = NS(
        **scope,
        sampling_intent_id=sampling.id,
        worker_identity_canonical=canonical_bytes(asdict(worker)).decode(),
        worker_identity_sha256=digest(asdict(worker)),
        controller_identity_sha256=digest(asdict(binding)),
    )
    cursor = NS(
        **scope, id=sampling.sampling_state_id, phase="sampling", sequence=2, frontier=0
    )
    session = NS(
        get=lambda cls, key: (
            sampling if cls is service.OEStructuredSamplingIntent else parent
        ),
        scalar=lambda stmt: (
            placement
            if stmt.column_descriptions[0]["entity"]
            is service.OEStructuredWorkerPlacement
            else cursor
        ),
    )
    calls = []
    monkeypatch.setattr(
        service,
        "require_controller_context",
        lambda *a, **kw: (
            calls.append(kw) or NS(epoch=NS(controller_admission_id=admission))
        ),
    )
    descriptor = WorkerDescriptor(
        schema_version="mineagent.oe-structured-iteration-grant.v1",
        admission_id=admission,
        epoch_id=epoch,
        intent_id=sampling.id,
        intent_ticket="c" * 64,
        native_iteration=1,
        parent_candidate_id=parent.id,
        parent_candidate_sha256=parent.candidate_sha256,
        selection_sha256=sampling.selection_sha256,
    ).model_dump(mode="json")

    def call():
        return service.require_worker_descriptor(
            session, NS(**scope), descriptor, NS(), peer=peer
        )

    return NS(**locals())


def test_descriptor_requires_exact_worker_and_rechecks_controller(monkeypatch):
    c = fixture(monkeypatch)
    assert c.call() is c.sampling
    assert len(c.calls) == 2 and all(
        x == {"peer": c.peer, "intent_id": c.sampling.id} for x in c.calls
    )


@pytest.mark.parametrize(
    "change",
    ["ticket", "parent", "cursor", "pid", "controller", "role", "extra", "bool"],
)
def test_wire_or_placement_drift_is_not_authority(monkeypatch, change):
    c = fixture(monkeypatch)
    if change == "ticket":
        c.descriptor["intent_ticket"] = "f" * 64
    elif change == "parent":
        c.descriptor["parent_candidate_id"] = str(uuid4())
    elif change == "cursor":
        c.cursor.phase = "applied"
    elif change == "pid":
        c.placement.worker_identity_sha256 = "0" * 64
    elif change == "controller":
        c.placement.controller_identity_sha256 = "0" * 64
    elif change == "role":
        c.peer.binding = c.binding
    elif change == "extra":
        c.descriptor["pid"] = 102
    elif change == "bool":
        c.descriptor["native_iteration"] = True
    with pytest.raises((ProcessError, ValueError)):
        c.call()
