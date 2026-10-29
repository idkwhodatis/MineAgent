"""Normal027 request/origin boundaries, no fake run/submit or physical acceptance."""

from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app.oe_structured_submission_schemas import (
    StructuredSearchRequest,
    StructuredSearchSubmit,
    StructuredSubmissionReceipt,
)
from backend.app.services import DomainError


def request():
    return StructuredSearchRequest(
        snapshot_id=uuid4(),
        seed_parameters={
            "threshold": 0.0,
            "top_k": 1,
            "maximum_weight": 0.5,
            "maximum_gross_weight": 1.0,
        },
        objective={
            "window": {"start": date(2024, 3, 13), "end": date(2024, 3, 15)},
            "initial_cash": 10000.0,
        },
        budget={
            "calls": 1,
            "tokens": 100000,
            "process_slots": 3,
            "evaluator_executions": 2,
            "mutation_slots": 1,
            "storage_bytes": 2**30,
            "wall_milliseconds": 780000,
            "cost_microunits": 0,
        },
        maximum_mutation_slots=1,
    )


@pytest.mark.parametrize(
    "field",
    [
        "owner_id",
        "runtime_image",
        "python_source",
        "source_policy",
        "origin",
        "approved",
        "pin",
    ],
)
def test_user_request_cannot_select_authority_or_override_origin(field):
    raw = request().model_dump()
    raw[field] = "caller-value"
    with pytest.raises(ValueError):
        StructuredSearchRequest.model_validate(raw)


@pytest.mark.parametrize("value", [True, 1.0, "1", -1])
def test_budget_is_exact_nonnegative_integer(value):
    raw = request().model_dump()
    raw["budget"]["calls"] = value
    with pytest.raises(ValueError):
        StructuredSearchRequest.model_validate(raw)


def test_seed_only_four_parameters_host_selects_actual_signal_window():
    declaration = request()
    program = declaration.seed_parameters.program(
        {"start": date(2024, 3, 13), "end": date(2024, 3, 14)}
    )
    assert program.score_operator == "identity_factor_value_descending"
    assert program.outside_signal_window == "hold_without_factor_read"
    with pytest.raises(ValueError):
        declaration.seed_parameters.model_copy(update={"top_k": True}).program(
            program.signal_window
        )


def test_submit_keeps_exact_original_request_and_not_preflight_as_user_content():
    declaration = request()
    submitted = StructuredSearchSubmit(
        **declaration.model_dump(), preflight_sha256="a" * 64
    )
    assert submitted.request() == declaration
    assert submitted.request().sha256 == declaration.sha256
    receipt = StructuredSubmissionReceipt(
        id=uuid4(),
        run_id=uuid4(),
        request_sha256=declaration.sha256,
        preflight_sha256="a" * 64,
        normalized_request=declaration,
        origin_user_id=uuid4(),
    )
    assert receipt.origin == "authenticated_user_submission"
    with pytest.raises(ValueError):
        StructuredSubmissionReceipt.model_validate(
            {**receipt.model_dump(), "request_sha256": "b" * 64}
        )


def test_unknown_or_other_owner_snapshot_never_invokes_historical_reader(monkeypatch):
    from backend.app import oe_structured_preflight_data as module

    @contextmanager
    def factory():
        yield SimpleNamespace(scalar=lambda statement: None)

    def unexpected(*args, **kwargs):
        raise AssertionError("must not read/disclose another owner data")

    monkeypatch.setattr(module, "disclose", unexpected)
    with pytest.raises(DomainError) as caught:
        module.resolve_preflight_data(factory, uuid4(), request(), SimpleNamespace())
    assert caught.value.status_code == 404


def test_existing_snapshot_requires_prior_explicit_release_before_market_reads(
    monkeypatch,
):
    from backend.app import oe_structured_preflight_data as module

    @contextmanager
    def factory():
        rows = iter(
            (
                SimpleNamespace(run_id=uuid4()),
                SimpleNamespace(
                    purpose="factor_research",
                    engine="rdagent",
                    config={"schema_version": "mineagent.factor-research.v2"},
                ),
            )
        )
        yield SimpleNamespace(scalar=lambda statement: next(rows))

    def not_released(*args, **kwargs):
        assert kwargs["create_release"] is False
        raise DomainError("engine_result_release_required", "Release required", 409)

    monkeypatch.setattr(module, "disclose", not_released)
    with pytest.raises(DomainError) as caught:
        module.resolve_preflight_data(factory, uuid4(), request(), SimpleNamespace())
    assert caught.value.code == "engine_result_release_required"
