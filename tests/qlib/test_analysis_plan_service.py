"""Plan ordering/immutability doubles; not PostgreSQL race acceptance."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app import factor_analysis_services as service
from backend.app import rdagent_configured_source as source
from backend.app.factor_analysis_models import FactorAnalysisPlan
from backend.app.models import ResearchRun
from backend.app.rdagent_models import FactorGenerationStart
from backend.app.services import DomainError
from packages.factors.contracts import DateWindow
from packages.qlib_adapter.objective import DirectObjectiveDeclaration
from packages.rdagent_adapter.configured_source import ConfiguredSourcePolicy


@pytest.fixture
def context(monkeypatch):
    fence = SimpleNamespace(owner_id=uuid4(), run_id=uuid4())
    window = DateWindow(start="2024-03-12", end="2024-03-13")
    config = SimpleNamespace(
        sha256="a" * 64,
        input=SimpleNamespace(
            sha256="b" * 64,
            window=window,
            dataset=SimpleNamespace(
                dataset_version_id=uuid4(), content_sha256="c" * 64
            ),
        ),
        runtime=SimpleNamespace(
            analysis_image="sha256:" + "d" * 64,
            dependency_locks=[SimpleNamespace(role="analysis", sha256="e" * 64)],
        ),
    )
    spec = SimpleNamespace(id=uuid4())
    monkeypatch.setattr(service, "_require", lambda *a: None)
    monkeypatch.setattr(service, "require_research_spec", lambda *a: (spec, config))
    from backend.app import rdagent_configured_exposure

    monkeypatch.setattr(
        rdagent_configured_exposure,
        "record_configured_source_exposure",
        lambda *args: None,
    )
    reads = []
    monkeypatch.setattr(
        source, "resolve_configured_source", lambda *a: reads.append("source")
    )
    monkeypatch.setattr(
        source, "resolve_configured_labels", lambda *a: reads.append("labels")
    )

    class Session:
        row = None
        started = None

        def scalar(self, q):
            entity = q.column_descriptions[0]["entity"]
            if entity is ResearchRun:
                return SimpleNamespace(id=fence.run_id)
            if entity is FactorGenerationStart:
                return self.started
            assert entity is FactorAnalysisPlan
            return self.row

        def add(self, row):
            self.row = row

        def flush(self):
            pass

    session = Session()
    kwargs = {
        "objective": DirectObjectiveDeclaration(
            window=window,
            label_horizon_sessions=1,
            metric="ic",
            direction="at_least",
            threshold=0.1,
            minimum_defined_days=1,
            minimum_pairs=2,
        ),
        "source_policy": ConfiguredSourcePolicy(acknowledged_unverified=True),
        "qlib_installed_parent_image_id": "sha256:" + "f" * 64,
        "settings": object(),
    }
    return session, fence, kwargs, reads


def test_plan_is_flush_only_and_replay_rereads_source(context):
    session, fence, kwargs, reads = context
    first = service.prepare_analysis_plan(session, fence, **kwargs)
    assert service.prepare_analysis_plan(session, fence, **kwargs) == first
    assert reads == ["source", "labels", "source", "labels"]


def test_changed_objective_cannot_reuse_plan(context):
    session, fence, kwargs, _ = context
    service.prepare_analysis_plan(session, fence, **kwargs)
    kwargs["objective"] = kwargs["objective"].model_copy(update={"threshold": 0.9})
    with pytest.raises(DomainError):
        service.prepare_analysis_plan(session, fence, **kwargs)


def test_new_plan_after_generation_is_rejected(context):
    session, fence, kwargs, reads = context
    session.started = uuid4()
    with pytest.raises(DomainError):
        service.prepare_analysis_plan(session, fence, **kwargs)
    assert session.row is None and not reads


def test_parent_pin_is_not_an_arbitrary_image_reference(context):
    session, fence, kwargs, reads = context
    kwargs["qlib_installed_parent_image_id"] = "latest"
    with pytest.raises(DomainError):
        service.prepare_analysis_plan(session, fence, **kwargs)
    assert session.row is None and not reads
