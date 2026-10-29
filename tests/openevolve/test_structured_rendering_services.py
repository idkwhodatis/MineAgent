"""Completion relationships with real codecs and DB/peer boundary doubles."""

from dataclasses import asdict
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import oe_structured_rendering_services as service
from packages.factors.contracts import canonical_bytes, digest
from packages.openevolve_structured.contracts import SearchOperationContext
from packages.research_runtime.process_contracts import ProcessError
from runtimes.openevolve_structured_host.peer import WorkerBinding
from tests.openevolve.test_structured_provider_contracts import declared
from tests.openevolve.test_structured_worker_observation import case  # noqa: F401


@pytest.fixture
def scene(case, monkeypatch):  # noqa: F811
    _, _, _, _, _, rendered, context, _ = declared()
    scope = {
        "owner_id": context.owner_id,
        "run_id": context.run_id,
        "attempt_id": uuid4(),
        "generation": 1,
    }
    selection = {"snapshot": {"programs": {}}}
    sample = NS(
        **scope,
        id=context.iteration_intent_id,
        epoch_id=context.epoch_id,
        ordinal=1,
        sampling_state_id=uuid4(),
        parent_candidate_id=context.parent_candidate_id,
        selection_canonical=canonical_bytes(selection).decode(),
        selection_sha256=digest(selection),
        worker_ticket_sha256="f" * 64,
    )
    binding = WorkerBinding(
        case.binding,
        sample.epoch_id,
        sample.id,
        sample.worker_ticket_sha256,
        case.worker,
    )
    peer = NS(binding=binding)
    placement = NS(
        **scope,
        worker_identity_canonical=canonical_bytes(asdict(case.worker)).decode(),
        worker_identity_sha256=digest(asdict(case.worker)),
        controller_identity_sha256=digest(asdict(case.binding)),
    )
    parent = NS(
        **scope,
        id=sample.parent_candidate_id,
        epoch_id=sample.epoch_id,
        candidate_sha256=rendered.current_candidate_sha256,
    )
    cursor = NS(
        **scope, id=sample.sampling_state_id, phase="sampling", sequence=2, frontier=0
    )
    state = NS(row=None)
    calls = []

    class Session:
        def get(self, cls, key):
            if cls is service.OEStructuredSamplingIntent:
                return sample
            if cls is service.OEStructuredCandidate:
                return parent
            return state.row

        def scalar(self, stmt):
            cls = stmt.column_descriptions[0]["entity"]
            return cursor if cls is service.OEStructuredControlState else placement

        def add(self, row):
            state.row = row

        def flush(self):
            calls.append("flush")

    monkeypatch.setattr(
        service,
        "require_controller_context",
        lambda *a, **kw: NS(
            config=NS(
                search=NS(execution_binding_sha256=context.execution_binding_sha256)
            )
        ),
    )

    def require(*a, **kw):
        calls.append("full_guard")
        return NS(
            context=SearchOperationContext.model_validate_json(
                state.row.context_canonical
            )
        )

    monkeypatch.setattr(service, "require_provider_operation", require)

    def call(raw=None):
        return service.record_rendering(
            Session(),
            NS(**scope),
            sample.id,
            canonical_bytes(rendered) if raw is None else raw,
            NS(),
            peer=peer,
        )

    return NS(
        call=call,
        state=state,
        calls=calls,
        peer=peer,
        placement=placement,
        sample=sample,
        cursor=cursor,
        rendered=rendered,
    )


def test_actual_placed_worker_closes_only_its_own_sampling_intent(scene):
    row, context = scene.call()
    assert row.id == scene.sample.id == context.iteration_intent_id
    assert scene.calls == ["flush", "full_guard"]
    assert scene.call()[0] is row
    assert scene.calls.count("flush") == 1


@pytest.mark.parametrize(
    "change", ["controller", "placement", "ticket", "cursor", "raw"]
)
def test_no_self_reported_or_stale_rendered_identity(scene, change):
    raw = None
    if change == "controller":
        scene.peer.binding = scene.peer.binding.controller
    elif change == "placement":
        scene.placement.worker_identity_sha256 = "0" * 64
    elif change == "ticket":
        scene.sample.worker_ticket_sha256 = "0" * 64
    elif change == "cursor":
        scene.cursor.id = uuid4()
    elif change == "raw":
        raw = canonical_bytes(scene.rendered) + b"\n"
    with pytest.raises(ProcessError):
        scene.call(raw)
    assert scene.state.row is None


def test_existing_rendered_intent_cannot_be_replaced(scene):
    row, _ = scene.call()
    changed = scene.rendered.model_copy(update={"user": "changed"})
    with pytest.raises(ProcessError):
        scene.call(canonical_bytes(changed))
    assert scene.state.row is row
