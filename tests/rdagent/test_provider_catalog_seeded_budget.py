"""Catalog-to-preflight budget flow with retained parent and isolated DB doubles."""

# ruff: noqa: F811
import sys
from types import SimpleNamespace

import pytest
from test_controller_profile_v4 import V3, V4, binding, server
from test_engine_run_preflight import request_body  # noqa: F401
from test_rdagent_configured_source import retained  # noqa: F401
from test_rdagent_configured_source_service import setup_source  # noqa: F401
from test_rdagent_provider_preflight_service import preflight_inputs  # noqa: F401
from test_rdagent_seed_parent import resolve, retained_parent  # noqa: F401

from backend.app import rdagent_provider_preflight as preflight
from backend.app import rdagent_provider_profile as profiles
from backend.app.rdagent_provider_catalog import provider_options
from backend.app.rdagent_provider_submission_contracts import parse_provider_request
from backend.app.services import DomainError


def setup_catalog(monkeypatch, owner, selection):
    model = binding()
    settings = SimpleNamespace(
        rdagent_research_enabled=True,
        rdagent_provider_python=sys.executable,
        rdagent_provider_runtime_profile=server(**selection).model_dump(mode="json"),
        rdagent_provider_profiles=[
            SimpleNamespace(
                connection=SimpleNamespace(id=model.profile_id, owner_ids=[owner])
            )
        ],
    )
    declaration = lambda *a, **kw: (None, model)
    monkeypatch.setattr(profiles, "resolve_provider_declaration", declaration)
    monkeypatch.setattr(preflight, "resolve_provider_declaration", declaration)
    return settings, model


@pytest.mark.parametrize(
    "selection,delta",
    [
        ({}, 0),
        ({"controller_profile": V3}, 0),
        ({"controller_profile": V3, "seeded_controller_profile": V4}, 60000),
    ],
)
def test_catalog_distinguishes_only_seeded_wall_budget(
    monkeypatch, preflight_inputs, selection, delta
):
    owner, *_ = preflight_inputs
    settings, _ = setup_catalog(monkeypatch, owner, selection)
    (option,) = provider_options(settings, owner, "027c_seeded_controller_v4")
    fresh, seeded = (
        option.recommended_budget.model_dump(),
        option.seeded_recommended_budget.model_dump(),
    )
    assert seeded.pop("wall_milliseconds") - fresh.pop("wall_milliseconds") == delta
    assert fresh == seeded
    assert option.can_create and option.seeded_can_create


def test_seeded_unavailable_on_old_schema_does_not_disable_fresh(
    monkeypatch, preflight_inputs
):
    owner, *_ = preflight_inputs
    settings, _ = setup_catalog(
        monkeypatch, owner, {"controller_profile": V3, "seeded_controller_profile": V4}
    )
    (option,) = provider_options(settings, owner, "027b_provider_material")
    assert option.can_create and option.recommended_budget is not None
    assert not option.seeded_can_create and option.seeded_recommended_budget is None
    assert option.seeded_reason_code == "runtime_schema_unavailable"


def test_disabled_runtime_cannot_offer_seed_budget(monkeypatch, preflight_inputs):
    owner, *_ = preflight_inputs
    settings, _ = setup_catalog(monkeypatch, owner, {})
    settings.rdagent_research_enabled = False
    (option,) = provider_options(settings, owner, "027c_seeded_controller_v4")
    assert not option.seeded_can_create and option.seeded_recommended_budget is None


def test_unpriced_model_has_no_fresh_or_seed_quote(monkeypatch, preflight_inputs):
    owner, *_ = preflight_inputs
    settings, model = setup_catalog(monkeypatch, owner, {})
    model = type(model).model_validate(
        {**model.model_dump(mode="json"), "pricing": {"status": "unavailable"}}
    )

    def declaration(*a, require_enabled=True, **kw):
        if require_enabled:
            raise DomainError("rd_model_pricing_unavailable", "Unknown pricing", 409)
        return None, model

    monkeypatch.setattr(profiles, "resolve_provider_declaration", declaration)
    (option,) = provider_options(settings, owner, "027c_seeded_controller_v4")
    assert option.recommended_budget is option.seeded_recommended_budget is None
    assert not option.can_create and not option.seeded_can_create


def test_catalog_budget_reaches_real_preflight_and_old_fresh_quote_fails_seed(
    monkeypatch, preflight_inputs, retained_parent
):
    owner, request, _, _, _ = preflight_inputs
    settings, model = setup_catalog(
        monkeypatch, owner, {"controller_profile": V3, "seeded_controller_profile": V4}
    )
    (option,) = provider_options(settings, owner, "027c_seeded_controller_v4")
    parent = resolve(retained_parent)
    monkeypatch.setattr(preflight, "resolve_seed_parent", lambda *a: parent)
    session = SimpleNamespace(
        scalars=lambda *a: SimpleNamespace(all=lambda: ["027c_seeded_controller_v4"])
    )
    value = {
        **request.model_dump(mode="json"),
        "model_profile_id": model.profile_id,
        "budget": option.recommended_budget.model_dump(mode="json"),
    }
    fresh = preflight.provider_preflight(
        session, owner, parse_provider_request(value), settings
    )
    assert fresh.can_submit and not fresh.reasons
    value.update(
        schema_version="mineagent.rdagent-provider-seeded-submission.v1",
        parent_factor_version_id=str(parent.reference.factor_version_id),
    )
    insufficient = preflight.provider_preflight(
        session, owner, parse_provider_request(value), settings
    )
    assert not insufficient.can_submit
    assert [r.code for r in insufficient.reasons] == ["budget_below_profile"]
    value["budget"] = option.seeded_recommended_budget.model_dump(mode="json")
    seeded = preflight.provider_preflight(
        session, owner, parse_provider_request(value), settings
    )
    assert seeded.can_submit and not seeded.reasons
    assert seeded.normalized_request.budget == option.seeded_recommended_budget
    assert seeded.parent_binding_sha256 == parent.reference.sha256
    assert seeded.runtime_profile_sha256 != fresh.runtime_profile_sha256


@pytest.mark.parametrize(
    "revision",
    [
        "027c_seeded_controller_v4",
        "028_oe_structured_search",
        "029_research_workflow",
        "030_declared_upload_basket",
        "031_workflow_structured_stage",
        "032_normal_oe_note_pins",
    ],
)
def test_seeded_budget_exact_successor_schema(monkeypatch, preflight_inputs, revision):
    owner, *_ = preflight_inputs
    settings, _ = setup_catalog(
        monkeypatch, owner, {"controller_profile": V3, "seeded_controller_profile": V4}
    )
    (option,) = provider_options(settings, owner, revision)
    assert option.can_create and option.seeded_can_create
    assert (
        option.seeded_recommended_budget.wall_milliseconds
        == option.recommended_budget.wall_milliseconds + 60000
    )


@pytest.mark.parametrize(
    "revision", ["UNKNOWN", "033_safe_workflow_branches", "027b_provider_material"]
)
def test_seeded_budget_rejects_uninstalled_revision(
    monkeypatch, preflight_inputs, revision
):
    owner, *_ = preflight_inputs
    settings, _ = setup_catalog(
        monkeypatch, owner, {"controller_profile": V3, "seeded_controller_profile": V4}
    )
    (option,) = provider_options(settings, owner, revision)
    assert not option.seeded_can_create and option.seeded_recommended_budget is None
