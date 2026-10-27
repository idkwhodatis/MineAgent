"""Accepted provider replay uses immutable inputs, not today's active steer head."""

# ruff: noqa: F811
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_engine_run_preflight import request_body  # noqa: F401
from test_rdagent_configured_source import retained  # noqa: F401
from test_rdagent_configured_source_service import setup_source  # noqa: F401
from test_rdagent_submission_receipts import replay  # noqa: F401

from backend.app.rdagent_provider_profile import RDProviderServerRuntimeProfile
from backend.app.rdagent_provider_submission_contracts import (
    CATALOG,
    RDProviderSubmissionRequest,
    RDProviderSubmit,
    provider_commitment,
)
from backend.app.rdagent_submission_receipts import existing_receipt, read_request
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.rdagent_adapter.provider_config import ProviderFactorResearchConfig
from packages.rdagent_adapter.steer import SteerSnapshot, SteerVersion
from packages.rdagent_provider.contracts import RDModelBinding


@pytest.fixture
def provider_saved(replay):
    import hashlib

    old = read_request(replay.row)
    guidance = "Test downside dispersion, not momentum."
    steer = SteerVersion(
        steer_id=uuid4(),
        version_id=uuid4(),
        ordinal=1,
        kind="hypothesis",
        text=guidance,
        text_sha256=hashlib.sha256(guidance.encode()).hexdigest(),
    )
    snapshot = SteerSnapshot(owner_id=replay.owner, versions=(steer,))
    binding = RDModelBinding(
        profile_id="owned-model",
        profile_sha256="b" * 64,
        chat_model="chat",
        embedding_model="embed",
        embedding_dimensions=3,
        max_output_tokens=128,
        request_deadline_ms=8000,
        synthetic=True,
        pricing={"status": "unavailable"},
    )
    request = RDProviderSubmissionRequest.model_validate(
        {
            **old.model_dump(mode="json"),
            "schema_version": "mineagent.rdagent-provider-submission.v1",
            "model_profile_id": binding.profile_id,
            "selected_steer_version_ids": [str(steer.version_id)],
        }
    )
    config = ProviderFactorResearchConfig.model_validate(
        {
            **replay.run.config,
            "schema_version": "mineagent.factor-research.v2",
            "model_profile": "configured.rdagent.v1",
            "model_binding": binding.model_dump(mode="json"),
            "steer_snapshot": snapshot.model_dump(mode="json"),
            "steer_snapshot_sha256": snapshot.sha256,
        }
    )
    replay.run.config = config.model_dump(mode="json")
    replay.run.request_sha256 = config.sha256
    replay.row.config_sha256 = replay.plan.config_sha256 = config.sha256
    replay.row.request_canonical = canonical_bytes(request).decode()
    replay.row.request_sha256 = request.sha256
    replay.row.catalog_revision = CATALOG
    replay.row.runtime_profile_sha256 = digest(
        RDProviderServerRuntimeProfile(
            runtime=config.runtime,
            qlib_installed_parent_image_id=replay.plan.qlib_installed_parent_image_id,
        )
    )
    replay.row.preflight_sha256 = provider_commitment(
        replay.owner,
        request,
        dataset_sha256=config.input.dataset.content_sha256,
        input_sha256=config.input.sha256,
        profile_sha256=replay.row.runtime_profile_sha256,
        model_binding_sha256=binding.sha256,
        steer_snapshot_sha256=snapshot.sha256,
    )
    replay.submitted = RDProviderSubmit(
        **request.model_dump(), preflight_sha256=replay.row.preflight_sha256
    )
    return SimpleNamespace(saved=replay, config=config, snapshot=snapshot)


def test_provider_accepted_replay_needs_no_current_profile_or_active_steer(
    provider_saved,
):
    p = provider_saved
    # The strict session has no current profile, steer-root query or retained reads.
    result = existing_receipt(
        p.saved.session, p.saved.owner, "stable-key", p.saved.submitted
    )
    assert result.schema_version == "mineagent.rdagent-provider-submission-receipt.v1"
    assert result.model_binding_sha256 == p.config.model_binding.sha256
    assert result.replayed and result.status == "succeeded"


@pytest.mark.parametrize(
    "field", ["steer_snapshot_sha256", "model_binding", "schema_version"]
)
def test_provider_frozen_identity_drift_rejected(provider_saved, field):
    p = provider_saved
    p.saved.run.config[field] = "f" * 64
    with pytest.raises(DomainError):
        existing_receipt(
            p.saved.session, p.saved.owner, "stable-key", p.saved.submitted
        )
