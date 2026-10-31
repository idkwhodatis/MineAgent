"""Actual reservation/codec and filesystem guards, DB/fence/accounting doubles."""

from types import SimpleNamespace as NS
from uuid import UUID, uuid4

import pytest
from sqlalchemy.sql.elements import TextClause

from backend.app import research_object_services as module
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.rdagent_adapter.numeric_failure import RDNumericFailureFeedback
from tests.rdagent_provider.test_numeric_failure_contracts import frame


@pytest.fixture
def reservation(tmp_path, monkeypatch):
    from backend.app import (
        openevolve_process_quota,
        rdagent_services,
        research_broker_services,
    )

    (tmp_path / "research-objects").mkdir(mode=0o700)
    fence = NS(owner_id=uuid4(), run_id=uuid4(), attempt_id=uuid4(), generation=1)
    observed = frame()
    document = RDNumericFailureFeedback(
        **vars(fence),
        start_id=uuid4(),
        controller_launch_id=uuid4(),
        final_decision_call_id=uuid4(),
        failure=observed,
        code_sha256="c" * 64,
        input_sha256="d" * 64,
        upstream_feedback=canonical_bytes(
            {
                "execution": observed.feedback_text,
                "return_checking": None,
                "code": "actual critique",
                "final_decision": False,
            }
        ).decode(),
    )
    state = NS(
        revisions=["032b_oe_append_run_state"],
        added=[],
        guards=[],
        held=[],
        previous=None,
    )

    class Session:
        def scalars(self, query):
            return NS(
                all=lambda: state.revisions if isinstance(query, TextClause) else []
            )

        def scalar(self, query):
            from backend.app.research_object_models import ResearchObject

            if query.column_descriptions[0]["entity"] is ResearchObject:
                return state.read_object
            return state.previous

        def add(self, row):
            state.added.append(row)

        def flush(self):
            pass

    session = Session()
    settings = NS(
        storage_root=tmp_path,
        neutral_object_max_files=100,
        neutral_object_owner_max_bytes=1_000_000,
        neutral_object_installation_max_bytes=1_000_000,
    )
    monkeypatch.setattr(
        module,
        "require_neutral_transaction",
        lambda *a, **kw: state.guards.append((a, kw)),
    )
    monkeypatch.setattr(
        rdagent_services, "require_research_spec", lambda *a: state.guards.append(a)
    )
    monkeypatch.setattr(
        research_broker_services,
        "require_reservation",
        lambda *a, **kw: state.held.append((a, kw)),
    )
    monkeypatch.setattr(openevolve_process_quota, "combined_holds", lambda *a: [])
    state.document, state.fence, state.settings = document, fence, settings
    state.session = session
    state.call = lambda raw=None: module.reserve_object(
        session,
        fence,
        "factor_generation_feedback",
        canonical_bytes(document) if raw is None else raw,
        UUID(int=99),
        settings,
    )
    return state


@pytest.mark.parametrize(
    "revision",
    [
        "028_oe_structured_search",
        "029_research_workflow",
        "030_declared_upload_basket",
        "031_workflow_structured_stage",
        "032_normal_oe_note_pins",
        "032a_rd_terminal_results",
        "032b_oe_append_run_state",
    ],
)
def test_numeric_failure_actual_reservation_keeps_exact_typed_bytes(
    reservation, revision
):
    reservation.revisions = [revision]
    row = reservation.call()
    assert reservation.added == [row]
    assert row.sha256 == digest(reservation.document)
    assert row.size_bytes == len(canonical_bytes(reservation.document))
    assert row.storage_key.endswith(row.sha256 + ".json")
    assert row.object_kind == "factor_generation_feedback"
    assert all(getattr(row, k) == v for k, v in vars(reservation.fence).items())
    assert reservation.held[0][1]["minimum"].storage_bytes == row.peak_bytes
    assert row.peak_bytes == 2 * row.size_bytes + 8192
    assert reservation.guards[0][1] == {"resource_scope": "objects"}
    reservation.previous = row
    assert reservation.call() is row
    assert len(reservation.added) == 1
    # The later reader must dispatch the same exact numeric-failure tag too.
    from backend.app.research_object_models import ResearchObject

    raw = canonical_bytes(reservation.document)
    path = reservation.settings.storage_root / row.storage_key
    path.parent.mkdir(parents=True, mode=0o700)
    path.write_bytes(raw)
    path.chmod(0o600)
    stored = ResearchObject(
        id=uuid4(),
        owner_id=reservation.fence.owner_id,
        object_kind=row.object_kind,
        sha256=row.sha256,
        storage_key=row.storage_key,
        size_bytes=row.size_bytes,
    )
    reservation.read_object = stored
    assert (
        module.read_research_object(
            reservation.session,
            reservation.fence,
            stored.id,
            stored.object_kind,
            stored.sha256,
            reservation.settings,
        )
        == raw
    )
    path.write_bytes(raw[:-1] + b" ")
    with pytest.raises(DomainError):
        module.read_research_object(
            reservation.session,
            reservation.fence,
            stored.id,
            stored.object_kind,
            stored.sha256,
            reservation.settings,
        )


@pytest.mark.parametrize(
    "revision",
    [
        "021_execution_snapshots",
        "022_factor_analysis",
        "027c_seeded_controller_v4",
        "033_future",
        None,
    ],
)
def test_numeric_failure_not_backported_or_future_activated(reservation, revision):
    reservation.revisions = [] if revision is None else [revision]
    with pytest.raises((ValueError, DomainError)):
        reservation.call()
    assert not reservation.added and not reservation.held


@pytest.mark.parametrize("field", ["owner_id", "run_id", "attempt_id", "generation"])
def test_numeric_failure_reservation_binds_complete_fence(reservation, field):
    changed = reservation.document.model_copy(
        update={field: 2 if field == "generation" else uuid4()}
    )
    with pytest.raises(DomainError) as caught:
        reservation.call(canonical_bytes(changed))
    assert caught.value.code == "rd_execution_object_scope"
    assert not reservation.added and not reservation.held


@pytest.mark.parametrize(
    "mutation", ["space", "future_tag", "invented_measurement", "oversize", "not_bytes"]
)
def test_numeric_failure_reservation_preserves_canonical_and_no_metric_contract(
    reservation, mutation
):
    raw = canonical_bytes(reservation.document)
    if mutation == "space":
        raw += b" "
    elif mutation == "future_tag":
        raw = raw.replace(
            b"rd-numeric-failure-feedback.v1", b"rd-numeric-failure-feedback.v2"
        )
    elif mutation == "invented_measurement":
        raw = raw.replace(b'"return_checking":null', b'"return_checking":"IC=1"')
        # upstream_feedback is a JSON string inside the outer canonical object.
        if raw == canonical_bytes(reservation.document):
            import json

            body = reservation.document.model_dump(mode="json")
            feedback = json.loads(body["upstream_feedback"])
            feedback["return_checking"] = "IC=1"
            body["upstream_feedback"] = canonical_bytes(feedback).decode()
            raw = canonical_bytes(body)
    elif mutation == "oversize":
        raw = b" " * 65537
    else:
        raw = raw.decode()
    with pytest.raises((ValueError, DomainError)):
        reservation.call(raw)
    assert not reservation.added and not reservation.held
