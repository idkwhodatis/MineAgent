from types import SimpleNamespace
from uuid import UUID

import pytest
from test_rdagent_contracts import research_dict

from packages.rdagent_adapter.contracts import FactorResearchConfig
from packages.rdagent_adapter.persisted_identity import (
    LEGACY_ONLY_FIELDS,
    require_source_identity,
)


def run():
    config = FactorResearchConfig.model_validate(research_dict())
    return SimpleNamespace(
        purpose="factor_research",
        engine="rdagent",
        config=config.model_dump(mode="json"),
        dataset_version_id=config.input.dataset.dataset_version_id,
        request_sha256=config.sha256,
        research_image_digest=None,
        **dict.fromkeys(LEGACY_ONLY_FIELDS),
    )


def test_exact_rd_persisted_identity_has_no_legacy_strategy():
    item = run()
    assert require_source_identity(item).sha256 == item.request_sha256


@pytest.mark.parametrize("field", LEGACY_ONLY_FIELDS)
def test_any_legacy_binding_rejected(field):
    item = run()
    setattr(item, field, UUID(int=999))
    with pytest.raises(ValueError):
        require_source_identity(item)


@pytest.mark.parametrize(
    "case", ["pair", "dataset", "request", "image", "missing_default", "bool"]
)
def test_persisted_identity_drift_rejected(case):
    item = run()
    if case == "pair":
        item.engine = "legacy"
    elif case == "dataset":
        item.dataset_version_id = UUID(int=999)
    elif case == "request":
        item.request_sha256 = "0" * 64
    elif case == "image":
        item.research_image_digest = "sha256:" + "0" * 64
    elif case == "missing_default":
        del item.config["schema_version"]
    elif case == "bool":
        item.config["upstream_workers"] = True
    with pytest.raises(ValueError):
        require_source_identity(item)


def test_internal_fence_accepts_exact_rd_but_legacy_guard_remains_closed():
    from datetime import timedelta

    from backend.app.models import utcnow
    from backend.app.research_lifecycle import _check_neutral_fence, require_legacy_run
    from backend.app.services import DomainError
    from packages.research_runtime.fences import AttemptFence

    fence = AttemptFence(UUID(int=1), UUID(int=2), UUID(int=3), 1, UUID(int=4))
    item = run()
    item.owner_id = fence.owner_id
    item.id = fence.run_id
    item.active_attempt_id = fence.attempt_id
    item.generation = fence.generation
    item.status = "running"
    attempt = SimpleNamespace(
        run_id=fence.run_id,
        id=fence.attempt_id,
        generation=1,
        lease_token=fence.lease_token,
        lease_expires_at=utcnow() + timedelta(minutes=1),
        status="running",
        finished_at=None,
    )
    _check_neutral_fence(item, attempt, fence)
    with pytest.raises(DomainError):
        require_legacy_run(item)
    attempt.lease_token = UUID(int=999)
    with pytest.raises(DomainError):
        _check_neutral_fence(item, attempt, fence)
