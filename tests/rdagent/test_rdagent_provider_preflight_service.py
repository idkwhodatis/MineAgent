"""Model preflight doubles: no network, process or DB acceptance claimed."""

# ruff: noqa: F811
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_engine_run_preflight import request_body  # noqa: F401
from test_rdagent_configured_source import retained  # noqa: F401
from test_rdagent_configured_source_service import setup_source  # noqa: F401

from backend.app import rdagent_provider_preflight as module
from backend.app.engine_run_submission_schemas import RDPreflight
from backend.app.rdagent_provider_submission_contracts import (
    RDProviderSubmissionRequest,
)
from backend.app.services import DomainError
from packages.rdagent_adapter.steer import SteerSnapshot


@pytest.fixture(
    params=[
        "026_rd_seeded_submissions",
        "028_oe_structured_search",
        "029_research_workflow",
    ]
)
def preflight_inputs(request_body, monkeypatch, request):
    revision = request.param
    owner = uuid4()
    request = RDProviderSubmissionRequest.model_validate(
        {
            **request_body.model_dump(mode="json"),
            "schema_version": "mineagent.rdagent-provider-submission.v1",
            "model_profile_id": "owned-model",
            "selected_steer_version_ids": [],
        }
    )
    snapshot = SteerSnapshot(owner_id=owner, versions=())
    base = RDPreflight(
        normalized_request=request_body,
        request_sha256=request_body.sha256,
        preflight_sha256="a" * 64,
        dataset_content_sha256="b" * 64,
        frozen_input_sha256=None,
        data_eligible=False,
        can_submit=False,
        reasons=({"code": "budget_below_profile", "message": "Old fixture budget"},),
        cutoff_count=1,
        required_process_units=4,
        output_window=request.window,
    )
    monkeypatch.setattr(module, "preflight", lambda *a: base)
    monkeypatch.setattr(module, "resolve_steer_snapshot", lambda *a: snapshot)

    def unavailable(*a):
        raise DomainError("rd_provider_disabled", "Not enabled", 409)

    monkeypatch.setattr(module, "resolve_provider_declaration", unavailable)
    session = SimpleNamespace(
        scalars=lambda *a: SimpleNamespace(all=lambda: [revision])
    )
    return owner, request, snapshot, base, session


def test_fixture_budget_result_does_not_poison_model_data_eligibility(preflight_inputs):
    owner, request, snapshot, _base, session = preflight_inputs
    result = module.provider_preflight(session, owner, request, object())
    assert result.data_eligible and not result.can_submit
    assert [r.code for r in result.reasons] == ["rd_provider_disabled"]
    assert result.model_binding is result.model_binding_sha256 is None
    assert result.steer_snapshot == snapshot


def test_actual_data_rejection_still_blocks_model_request(
    preflight_inputs, monkeypatch
):
    owner, request, _snapshot, base, session = preflight_inputs
    base = base.model_copy(
        update={
            "reasons": (
                module.ProviderPreflightReason(
                    code="insufficient_history", message="History absent"
                ),
            )
        }
    )
    monkeypatch.setattr(module, "preflight", lambda *a: base)
    result = module.provider_preflight(session, owner, request, object())
    assert not result.data_eligible and not result.can_submit
    assert result.reasons[0].code == "insufficient_history"


def test_queued_guidance_reads_immutable_versions_not_active_resolver(
    preflight_inputs, monkeypatch
):
    from backend.app import rdagent_steer_services

    owner, request, snapshot, _base, session = preflight_inputs
    calls = []

    def current_forbidden(*a):
        raise AssertionError("Queued run must not resolve current active steer")

    monkeypatch.setattr(module, "resolve_steer_snapshot", current_forbidden)
    monkeypatch.setattr(
        rdagent_steer_services,
        "verify_steer_snapshot",
        lambda s, o, v: calls.append((o, v)),
    )
    result = module.provider_preflight(
        session, owner, request, object(), accepted_steer_snapshot=snapshot
    )
    assert calls == [(owner, snapshot)]
    assert result.steer_snapshot == snapshot


def test_seeded_native_preflight_reuses_real_guard_without_source_flock(
    preflight_inputs, tmp_path, monkeypatch
):
    import fcntl

    from backend.app import rdagent_seed_services as seeds
    from backend.app.factor_native_io import (
        NativeStorageError,
        native_storage_guard,
        require_native_storage_guard,
    )
    from backend.app.rdagent_provider_submission_contracts import RDProviderSeedRequest
    from packages.rdagent_adapter.seed_parent import SeedParentReference

    owner, request, _, _, session = preflight_inputs
    request = RDProviderSeedRequest.model_validate(
        {
            **request.model_dump(mode="python"),
            "schema_version": "mineagent.rdagent-provider-seeded-submission.v1",
            "parent_factor_version_id": uuid4(),
        }
    )
    reference = SeedParentReference(
        owner_id=owner,
        factor_id=uuid4(),
        factor_version_id=request.parent_factor_version_id,
        ordinal=1,
        code_object_id=uuid4(),
        code_sha256="1" * 64,
        code_size_bytes=1,
        definition_sha256="2" * 64,
        source_provenance_sha256="3" * 64,
    )
    settings = SimpleNamespace(storage_root=tmp_path)
    (tmp_path / "factors").mkdir()

    def reread(s, own, identity, settings):
        require_native_storage_guard(settings.storage_root)
        assert (s, own, identity) == (session, owner, reference.factor_version_id)
        return SimpleNamespace(reference=reference)

    monkeypatch.setattr(seeds, "resolve_seed_parent_under_native_guard", reread)
    monkeypatch.setattr(
        module, "resolve_seed_parent", lambda *a: pytest.fail("No nested source flock")
    )
    with pytest.raises(NativeStorageError):
        module.provider_preflight_under_native_guard(session, owner, request, settings)
    with native_storage_guard(tmp_path), monkeypatch.context() as patch:
        patch.setattr(fcntl, "flock", lambda *a: pytest.fail("No reacquire"))
        result = module.provider_preflight_under_native_guard(
            session, owner, request, settings
        )
        assert result.parent == reference
