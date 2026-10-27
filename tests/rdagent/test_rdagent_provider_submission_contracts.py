# ruff: noqa: F811
from uuid import uuid4

import pytest
from test_engine_run_preflight import request_body  # noqa: F401
from test_rdagent_configured_source import retained  # noqa: F401
from test_rdagent_configured_source_service import setup_source  # noqa: F401

from backend.app.engine_run_schemas import RDModelProfileOption
from backend.app.rdagent_provider_submission_contracts import (
    RDProviderSeedSubmit,
    RDProviderSubmissionRequest,
    RDProviderSubmit,
)


def declaration(body):
    return {
        **body.model_dump(mode="json"),
        "schema_version": "mineagent.rdagent-provider-submission.v1",
        "model_profile_id": "owned-model",
        "selected_steer_version_ids": [],
    }


def test_explicit_normal_request_preserves_generic_task_and_steer_order(request_body):
    first, second = uuid4(), uuid4()
    raw = declaration(request_body)
    raw["task"] = {
        **raw["task"],
        "formulation": "std(volume)",
        "description": "Volume dispersion hypothesis",
    }
    raw["selected_steer_version_ids"] = [str(first), str(second)]
    result = RDProviderSubmissionRequest.model_validate(raw)
    assert result.selected_steer_version_ids == (first, second)
    assert result.research_declaration().task.formulation == "std(volume)"
    assert "model_profile_id" not in result.research_declaration().model_dump()
    submitted = RDProviderSubmit.model_validate({**raw, "preflight_sha256": "a" * 64})
    assert submitted.request() == result


@pytest.mark.parametrize(
    "field", ["base_url", "secret", "secret_env", "runtime", "sdk_python", "provider"]
)
def test_user_cannot_inject_operator_authority(request_body, field):
    with pytest.raises(ValueError):
        RDProviderSubmissionRequest.model_validate(
            {**declaration(request_body), field: "caller"}
        )


def test_seed_is_explicit_new_family_not_silent_parent_field(request_body):
    raw = {
        **declaration(request_body),
        "parent_factor_version_id": str(uuid4()),
        "preflight_sha256": "a" * 64,
    }
    with pytest.raises(ValueError):
        RDProviderSubmit.model_validate(raw)
    raw["schema_version"] = "mineagent.rdagent-provider-seeded-submission.v1"
    assert RDProviderSeedSubmit.model_validate(raw).request().parent_factor_version_id


def test_duplicate_or_excessive_steer_selection_rejected(request_body):
    raw = declaration(request_body)
    for ids in ([str(uuid4())] * 2, [str(uuid4()) for _ in range(9)]):
        with pytest.raises(ValueError):
            RDProviderSubmissionRequest.model_validate(
                {**raw, "selected_steer_version_ids": ids}
            )


def test_public_model_option_exposes_no_endpoint_secret_or_billing_claim():
    fields = RDModelProfileOption.model_fields
    assert not {"base_url", "secret_env", "api_key", "secret", "owner_ids"} & set(
        fields
    )
    assert fields["billing_status"].default == "unknown"


def test_preflight_serializes_exact_binding_hash_not_property(request_body):
    from backend.app.rdagent_provider_submission_contracts import RDProviderPreflight
    from packages.rdagent_adapter.steer import SteerSnapshot
    from packages.rdagent_provider.contracts import RDModelBinding

    request = RDProviderSubmissionRequest.model_validate(declaration(request_body))
    snapshot = SteerSnapshot(owner_id=uuid4(), versions=())
    binding = RDModelBinding(
        profile_id="owned-model",
        profile_sha256="a" * 64,
        chat_model="chat",
        embedding_model="embed",
        embedding_dimensions=3,
        max_output_tokens=128,
        request_deadline_ms=8000,
        synthetic=True,
        pricing={"status": "unavailable"},
    )
    fields = {
        "normalized_request": request,
        "request_sha256": request.sha256,
        "preflight_sha256": "b" * 64,
        "dataset_content_sha256": "c" * 64,
        "frozen_input_sha256": None,
        "data_eligible": False,
        "can_submit": False,
        "cutoff_count": 0,
        "required_process_units": 0,
        "output_window": request.window,
        "reasons": (),
        "parent": None,
        "parent_binding_sha256": None,
        "steer_snapshot": snapshot,
        "steer_snapshot_sha256": snapshot.sha256,
        "model_binding": binding,
        "model_binding_sha256": binding.sha256,
    }
    result = RDProviderPreflight(**fields)
    assert result.model_dump(mode="json")["model_binding_sha256"] == binding.sha256
    for value in (None, "d" * 64):
        with pytest.raises(ValueError):
            RDProviderPreflight(**{**fields, "model_binding_sha256": value})
    assert (
        RDProviderPreflight(
            **{**fields, "model_binding": None, "model_binding_sha256": None}
        ).model_binding
        is None
    )
