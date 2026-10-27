"""Control requests do not confer stop/publication/lease-reset authority."""

from types import SimpleNamespace

import pytest
from test_engine_run_preflight import request_body  # noqa:F401
from test_rdagent_configured_source import retained  # noqa:F401
from test_rdagent_configured_source_service import setup_source  # noqa:F401
from test_rdagent_submission_receipts import replay  # noqa:F401

from backend.app import rdagent_control_services as control
from backend.app.services import DomainError


@pytest.fixture(
    params=[
        "023_rd_submissions",
        "024_openevolve_initial",
        "025_structured_factor_strategy",
    ]
)
def state(request, replay, monkeypatch):  # noqa:F811
    r = replay
    r.run.active_attempt_id = None
    rows = iter([r.run, r.row.plan_id, None])

    class Session:
        def scalars(self, query):
            return SimpleNamespace(all=lambda: [request.param])

        def scalar(self, query):
            return next(rows)

        def flush(self):
            pass

    r.session = Session()
    r.events = []
    monkeypatch.setattr(control, "_try_neutral_lock", lambda *args: None)
    monkeypatch.setattr(control, "append_event", lambda *args: r.events.append(args[2]))
    return r


def test_queued_cancel_has_no_attempt_to_stop(state):
    r = state
    r.run.status = "queued"
    result = control.request_cancel(r.session, r.owner, r.run.id)
    assert result.status == "cancelled" and not result.pending
    assert r.run.active_attempt_id is None and r.events == ["rd_cancelled_before_claim"]


@pytest.mark.parametrize("status", ["preparing", "running", "finalizing"])
def test_active_cancel_only_requests_containment(state, status):
    r = state
    r.run.status = status
    result = control.request_cancel(r.session, r.owner, r.run.id)
    assert result.status == "cancel_requested" and result.pending
    assert r.events == ["rd_cancel_requested"]
    assert not hasattr(result, "sandbox_stopped")


@pytest.mark.parametrize(
    "status", ["cancel_requested", "succeeded", "failed", "cancelled", "interrupted"]
)
def test_repeat_or_terminal_control_does_not_mutate_evidence(state, status):
    r = state
    r.run.status = status
    result = control.request_cancel(r.session, r.owner, r.run.id)
    assert result.status == status and not r.events


def test_no_silent_checkpoint_resume(state):
    r = state
    r.run.status = "paused"
    with pytest.raises(DomainError) as exc:
        control.request_cancel(r.session, r.owner, r.run.id)
    assert exc.value.code == "engine_control_not_enabled" and not r.events


def test_legacy_identity_has_safe_not_found(state):
    state.run.engine = "legacy"
    with pytest.raises(DomainError) as exc:
        control.request_cancel(state.session, state.owner, state.run.id)
    assert exc.value.code == "not_found" and not state.events
