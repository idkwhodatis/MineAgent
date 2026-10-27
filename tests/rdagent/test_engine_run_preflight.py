"""Real retained-byte preflight, no public execution or database acceptance claim."""

from datetime import date
from uuid import uuid4

import pytest
from test_rdagent_configured_source import retained  # noqa:F401
from test_rdagent_configured_source_service import setup_source  # noqa:F401

from backend.app.engine_run_catalog import runtime_catalog
from backend.app.engine_run_preflight import RECOMMENDED_BUDGET, preflight
from backend.app.engine_run_submission_schemas import (
    RDSubmissionReceipt,
    RDSubmissionRequest,
)
from backend.app.services import DomainError
from packages.factors.contracts import DateWindow


@pytest.fixture
def request_body(setup_source):  # noqa:F811
    s = setup_source
    return RDSubmissionRequest(
        dataset_version_id=s.dataset.id,
        window=s.config.input.window,
        input_fields=("volume",),
        lookback_sessions=1,
        lag_sessions=0,
        task=s.config.task,
        objective=s.plan.objective,
        budget=RECOMMENDED_BUDGET,
        max_iterations=1,
        source_policy=s.args["policy"],
    )


def test_real_input_eligible_but_execution_explicitly_closed(
    setup_source,  # noqa: F811
    request_body,
):
    s = setup_source
    result = preflight(s.session, s.fence.owner_id, request_body, s.settings)
    assert result.data_eligible is True and result.can_submit is False
    assert [x.code for x in result.reasons] == [
        "runtime_disabled",
        "task_profile_unsupported",
    ]
    assert result.cutoff_count == 1 and result.required_process_units == 4
    assert (
        result.normalized_request == request_body
        and result.request_sha256 == request_body.sha256
    )
    assert preflight(s.session, s.fence.owner_id, request_body, s.settings) == result
    assert not hasattr(s.session, "add")


@pytest.mark.parametrize(
    "field", ["max_iterations", "lookback_sessions", "lag_sessions"]
)
def test_bool_not_integer_limits(request_body, field):
    raw = request_body.model_dump(mode="json")
    raw[field] = True
    with pytest.raises(ValueError):
        RDSubmissionRequest.model_validate(raw)


@pytest.mark.parametrize("field", ["runtime", "provider", "orchestrator_image"])
def test_no_user_runtime_or_provider_authority(request_body, field):
    raw = request_body.model_dump(mode="json")
    raw[field] = "chosen-by-caller"
    with pytest.raises(ValueError):
        RDSubmissionRequest.model_validate(raw)


def test_larger_window_rejected_not_truncated(setup_source, request_body):  # noqa:F811
    s = setup_source
    raw = request_body.model_dump(mode="json")
    raw["window"] = {"start": "2024-03-11", "end": "2024-03-13"}
    raw["objective"]["window"] = raw["window"]
    result = preflight(
        s.session, s.fence.owner_id, RDSubmissionRequest.model_validate(raw), s.settings
    )
    assert result.output_window == DateWindow(
        start=date(2024, 3, 11), end=date(2024, 3, 13)
    )
    assert result.cutoff_count == 2 and not result.data_eligible
    assert {"output_window_unsupported", "insufficient_history"} <= {
        x.code for x in result.reasons
    }


def test_missing_label_and_delayed_feature_reasons(setup_source, request_body):  # noqa:F811
    s = setup_source
    raw = request_body.model_dump(mode="json")
    raw["objective"]["label_horizon_sessions"] = 2
    raw["lag_sessions"] = 1
    result = preflight(
        s.session, s.fence.owner_id, RDSubmissionRequest.model_validate(raw), s.settings
    )
    assert {"insufficient_label_horizon", "delayed_output_unsupported"} <= {
        x.code for x in result.reasons
    }


def test_budget_floor_server_declared(setup_source, request_body):  # noqa:F811
    s = setup_source
    raw = request_body.model_dump(mode="json")
    raw["budget"]["process_slots"] = 3
    result = preflight(
        s.session, s.fence.owner_id, RDSubmissionRequest.model_validate(raw), s.settings
    )
    assert "budget_below_profile" in {x.code for x in result.reasons}
    cap = runtime_catalog().engines[0]
    assert (
        cap.can_preflight
        and not cap.can_create
        and cap.recommended_budget == RECOMMENDED_BUDGET
    )


def test_foreign_dataset_is_not_found(setup_source, request_body, monkeypatch):  # noqa:F811
    s = setup_source
    monkeypatch.setattr(s.session, "scalar", lambda query: None)
    with pytest.raises(DomainError) as exc:
        preflight(s.session, uuid4(), request_body, s.settings)
    assert exc.value.status_code == 404


@pytest.mark.parametrize(
    "status",
    ["succeeded", "interrupted", "pause_requested", "cancel_requested", "cancelled"],
)
def test_receipt_uses_actual_run_statuses(status):
    value = RDSubmissionReceipt(
        run_id=uuid4(),
        plan_id=uuid4(),
        request_sha256="a" * 64,
        preflight_sha256="b" * 64,
        replayed=True,
        status=status,
    )
    assert value.status == status


def test_fixture_task_rejection_is_not_silent_source_substitution(
    setup_source, request_body  # noqa:F811
):
    s = setup_source
    result = preflight(s.session, s.fence.owner_id, request_body, s.settings)
    assert result.data_eligible and not result.can_submit
    assert "task_profile_unsupported" in {r.code for r in result.reasons}
    valid = request_body.model_copy(
        update={
            "input_fields": ("close",),
            "task": request_body.task.model_copy(
                update={
                    "factor_kind": "time_series",
                    "formulation": "close[t]/close[t-1]-1",
                    "output_unit": "return",
                }
            ),
        }
    )
    result = preflight(s.session, s.fence.owner_id, valid, s.settings)
    assert result.data_eligible and "task_profile_unsupported" not in {
        r.code for r in result.reasons
    }
