"""Exact accepted replay is metadata-only; no new runtime or retained-byte access."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_engine_run_preflight import request_body  # noqa:F401
from test_rdagent_configured_source import retained  # noqa:F401
from test_rdagent_configured_source_service import setup_source  # noqa:F401

from backend.app.engine_run_submission_schemas import RDSubmit
from backend.app.factor_analysis_models import FactorAnalysisPlan
from backend.app.models import ResearchRun
from backend.app.rdagent_submission_receipts import (
    existing_receipt,
    read_request,
    validate_key,
)
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes, digest
from packages.rdagent_adapter.persisted_identity import LEGACY_ONLY_FIELDS


@pytest.fixture
def replay(setup_source, request_body):  # noqa:F811
    s = setup_source
    owner = s.fence.owner_id
    from backend.app.rdagent_server_profile import (
        ROLE_LOCKS,
        RDServerRuntimeProfile,
    )
    from packages.rdagent_adapter.contracts import DependencyLock

    runtime = s.config.runtime.model_copy(
        update={
            "dependency_locks": tuple(
                DependencyLock(role=role, sha256=sha)
                for role, sha in sorted(ROLE_LOCKS.items())
            )
        }
    )
    s.config = s.config.model_copy(update={"runtime": runtime})
    s.config = s.config.model_copy(
        update={
            "budget": request_body.budget,
            "max_iterations": request_body.max_iterations,
        }
    )
    run = SimpleNamespace(**{field: None for field in LEGACY_ONLY_FIELDS})
    run.__dict__.update(
        id=s.fence.run_id,
        owner_id=owner,
        purpose="factor_research",
        engine="rdagent",
        config=s.config.model_dump(mode="json"),
        dataset_version_id=s.dataset.id,
        request_sha256=s.config.sha256,
        research_image_digest=s.config.runtime.orchestrator_image,
        idempotency_key="stable-key",
        status="succeeded",
    )
    plan = SimpleNamespace(
        id=uuid4(),
        owner_id=owner,
        run_id=run.id,
        config_sha256=s.config.sha256,
        input_sha256=s.config.input.sha256,
        objective_sha256=request_body.objective.sha256,
        source_policy_sha256=digest(request_body.source_policy),
        qlib_installed_parent_image_id="sha256:" + "e" * 64,
    )
    row = SimpleNamespace(
        owner_id=owner,
        run_id=run.id,
        plan_id=plan.id,
        request_canonical=canonical_bytes(request_body).decode(),
        request_sha256=request_body.sha256,
        runtime_profile_sha256=digest(
            RDServerRuntimeProfile(
                runtime=runtime,
                qlib_installed_parent_image_id=plan.qlib_installed_parent_image_id,
            )
        ),
        catalog_revision="configured_rd_single_output_session_v1",
        preflight_sha256="a" * 64,
        config_sha256=s.config.sha256,
    )

    row.preflight_sha256 = digest(
        {
            "schema_version": "mineagent.rdagent-preflight-binding.v1",
            "owner_id": str(owner),
            "request_sha256": request_body.sha256,
            "dataset_content_sha256": s.config.input.dataset.content_sha256,
            "frozen_input_sha256": s.config.input.sha256,
            "catalog_revision": row.catalog_revision,
            "runtime_profile_sha256": row.runtime_profile_sha256,
        }
    )

    class Session:
        def scalar(self, query):
            return row

        def get(self, model, identity):
            if model is ResearchRun:
                return run
            assert model is FactorAnalysisPlan
            return plan

    submitted = RDSubmit(
        **request_body.model_dump(), preflight_sha256=row.preflight_sha256
    )
    return SimpleNamespace(
        session=Session(), owner=owner, row=row, run=run, plan=plan, submitted=submitted
    )


@pytest.mark.parametrize(
    "status",
    [
        "queued",
        "preparing",
        "running",
        "finalizing",
        "pause_requested",
        "paused",
        "cancel_requested",
        "cancelled",
        "failed",
        "interrupted",
        "succeeded",
    ],
)
def test_all_real_states_replay_without_source_or_runtime(replay, status):
    replay.run.status = status
    result = existing_receipt(
        replay.session, replay.owner, "stable-key", replay.submitted
    )
    assert result.replayed and result.accepted and result.status == status
    assert result.run_id == replay.run.id and result.plan_id == replay.plan.id


@pytest.mark.parametrize("key", ["", "a b", "a\n", "é", "a" * 129, None, True])
def test_key_grammar(key):
    with pytest.raises(DomainError, match="Idempotency-Key"):
        validate_key(key)


def test_changed_preflight_conflicts(replay):
    with pytest.raises(DomainError) as exc:
        existing_receipt(
            replay.session,
            replay.owner,
            "stable-key",
            replay.submitted.model_copy(update={"preflight_sha256": "b" * 64}),
        )
    assert exc.value.code == "idempotency_conflict"


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("row", "request_sha256", "b" * 64),
        ("row", "request_canonical", "{}"),
        ("run", "owner_id", uuid4()),
        ("plan", "run_id", uuid4()),
        ("plan", "objective_sha256", "c" * 64),
        ("run", "status", "completed"),
    ],
)
def test_corrupt_receipt_or_exact_graph_rejected(replay, target, field, value):
    setattr(getattr(replay, target), field, value)
    with pytest.raises(DomainError) as exc:
        existing_receipt(replay.session, replay.owner, "stable-key", replay.submitted)
    assert exc.value.code == "submission_receipt_drift"


def test_duplicate_keys_not_normalized(replay):
    replay.row.request_canonical = '{"schema_version":"x","schema_version":"y"}'
    with pytest.raises(DomainError):
        read_request(replay.row)


def test_absent_receipt_never_returns_unreceipted_run(replay):
    rows = iter([None, replay.run])
    replay.session.scalar = lambda query: next(rows)
    with pytest.raises(DomainError) as exc:
        existing_receipt(replay.session, replay.owner, "stable-key", replay.submitted)
    assert exc.value.code == "idempotency_conflict"


def test_unused_key_has_no_side_effect(replay):
    replay.session.scalar = lambda query: None
    assert (
        existing_receipt(replay.session, replay.owner, "unused", replay.submitted)
        is None
    )
