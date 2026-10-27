"""Real retained source/parent bytes with transaction doubles; not PG acceptance."""

from types import SimpleNamespace

import pytest
from test_engine_run_preflight import request_body  # noqa:F401
from test_rdagent_configured_source import retained  # noqa:F401
from test_rdagent_configured_source_service import setup_source  # noqa:F401
from test_rdagent_seed_parent import retained_parent  # noqa:F401
from test_rdagent_submission_service import queued_writer  # noqa:F401

from backend.app import rdagent_seed_preflight as preflight_module
from backend.app import rdagent_seed_submission as service
from backend.app.rdagent_seed_contracts import RDSeededSubmissionRequest, RDSeededSubmit
from backend.app.rdagent_seed_runtime_profile import RDSeededServerRuntimeProfile
from backend.app.services import DomainError


@pytest.fixture
def seeded_writer(queued_writer, retained_parent, monkeypatch):  # noqa:F811
    from backend.app import comparison_services
    from backend.app.factor_models import FactorVersion
    from backend.app.rdagent_seed_services import resolve_seed_parent

    q, r = queued_writer, retained_parent
    owner = q.source.fence.owner_id
    new_key = f"factors/{owner}/objects/{r.obj.sha256}.py"
    new_path = r.settings.storage_root / new_key
    new_path.parent.mkdir(parents=True)
    r.path.rename(new_path)
    r.path = new_path
    r.owner = r.version.owner_id = r.obj.owner_id = r.factor.owner_id = owner
    r.obj.storage_key = new_key

    # One fixture root owns real dataset files; parent is independently retained.
    def parent(session, owned, identity, settings):
        assert owned == owner and identity == r.version.id
        return resolve_seed_parent(r.session(), owned, identity, r.settings)

    monkeypatch.setattr(preflight_module, "resolve_seed_parent", parent)
    monkeypatch.setattr(service, "resolve_seed_parent", parent)
    original_preflight = preflight_module.preflight
    original_source = preflight_module.resolve_dataset_preflight
    monkeypatch.setattr(
        preflight_module,
        "preflight",
        lambda session, *a: original_preflight(q.source.session, *a),
    )
    monkeypatch.setattr(
        preflight_module,
        "resolve_dataset_preflight",
        lambda session, **kw: original_source(q.source.session, **kw),
    )
    monkeypatch.setattr(service, "_try_neutral_lock", lambda *a: None)
    monkeypatch.setattr(service, "existing_seeded_receipt", lambda *a: None)
    monkeypatch.setattr(comparison_services, "reject_comparison_key", lambda *a: None)
    q.session.scalars = lambda query: SimpleNamespace(
        all=lambda: [preflight_module.REVISION]
    )
    q.source.session.scalars = q.session.scalars
    get = q.session.get
    q.session.get = lambda model, identity: (
        r.version if model is FactorVersion else get(model, identity)
    )
    from backend.app.models import ResearchRun

    def flush():
        # Model the real SQL NULL after INSERT; the pure transaction double has
        # no driver to replace sqlalchemy.null() with the database value.
        for row in q.session.rows:
            if isinstance(row, ResearchRun):
                row.submission_binding = None

    q.session.flush = flush
    profile = RDSeededServerRuntimeProfile(
        runtime=q.source.config.runtime,
        qlib_installed_parent_image_id="sha256:" + "e" * 64,
    )
    q.source.settings.rdagent_seeded_runtime_profile = profile
    request = RDSeededSubmissionRequest.model_validate(
        {
            **q.body.request().model_dump(mode="json"),
            "schema_version": "mineagent.rdagent-seeded-submission.v1",
            "parent_factor_version_id": str(r.version.id),
        }
    )
    checked = preflight_module.seeded_preflight(
        q.session, owner, request, q.source.settings
    )
    assert checked.can_submit
    submitted = RDSeededSubmit(
        **request.model_dump(), preflight_sha256=checked.preflight_sha256
    )
    return q, r, submitted, checked


def test_seeded_queue_persists_actual_parent_and_no_fake_fresh_receipt(seeded_writer):
    from backend.app.models import (
        DataUsageExposure,
        ExecutionAttempt,
        OutboxEvent,
        ResearchRun,
    )
    from backend.app.rdagent_seed_models import FactorSeededSubmission
    from backend.app.rdagent_seed_services import read_saved_seed
    from backend.app.rdagent_submission_models import FactorResearchSubmission

    q, r, body, checked = seeded_writer
    receipt = service.submit_seeded_run(
        q.session, r.owner, body, "seeded-key", q.source.settings
    )
    rows = q.session.rows
    run = next(row for row in rows if isinstance(row, ResearchRun))
    seed = next(row for row in rows if isinstance(row, FactorSeededSubmission))
    assert (
        run.status == "queued" and run.active_attempt_id is None and run.generation == 1
    )
    assert not any(
        isinstance(row, (ExecutionAttempt, FactorResearchSubmission)) for row in rows
    )
    assert sum(isinstance(row, OutboxEvent) for row in rows) == 1
    assert sum(isinstance(row, DataUsageExposure) for row in rows) == 1
    assert receipt.parent_factor_version_id == r.version.id
    assert receipt.parent_factor_id == r.factor.id and not receipt.replayed
    request, ref = read_saved_seed(seed)
    assert request == body.request() and ref == checked.parent
    assert run.request_sha256 == seed.config_sha256 != seed.request_sha256


def test_parent_tamper_between_preflight_and_queue_creates_nothing(seeded_writer):
    q, r, body, _ = seeded_writer
    r.path.write_bytes(b"tampered after preflight")
    with pytest.raises(DomainError):
        service.submit_seeded_run(
            q.session, r.owner, body, "seeded-key", q.source.settings
        )
    assert not q.session.rows


def test_exact_seeded_replay_precedes_current_runtime_and_parent_reads(
    seeded_writer, monkeypatch
):
    q, r, body, _ = seeded_writer
    marker = object()
    monkeypatch.setattr(service, "existing_seeded_receipt", lambda *a: marker)
    monkeypatch.setattr(
        service,
        "resolve_seeded_runtime",
        lambda *a: pytest.fail("runtime on accepted replay"),
    )
    monkeypatch.setattr(
        service,
        "resolve_seed_parent",
        lambda *a: pytest.fail("parent on accepted replay"),
    )
    assert (
        service.submit_seeded_run(q.session, r.owner, body, "same-key", object())
        is marker
    )
    assert not q.session.rows


def test_seeded_profile_rejects_fresh_profile_as_authority(seeded_writer):
    q, _, _, _ = seeded_writer
    with pytest.raises(ValueError):
        RDSeededServerRuntimeProfile.model_validate(
            q.source.settings.rdagent_runtime_profile.model_dump()
        )


def test_changed_seeded_preflight_creates_nothing(seeded_writer):
    q, r, body, _ = seeded_writer
    body = body.model_copy(update={"preflight_sha256": "f" * 64})
    with pytest.raises(DomainError) as exc:
        service.submit_seeded_run(
            q.session, r.owner, body, "same-key", q.source.settings
        )
    assert exc.value.code == "preflight_changed" and not q.session.rows
