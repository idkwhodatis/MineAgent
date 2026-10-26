"""Inactive service transition tests, not execution or PostgreSQL acceptance."""

from types import SimpleNamespace
from uuid import UUID

import pytest

import backend.app.factor_services as factors
import backend.app.rdagent_evaluation_services as service
import backend.app.rdagent_services as domain
from backend.app.rdagent_models import FactorGenerationStart
from backend.app.services import DomainError
from packages.factors.contracts import ExposureHistory, SnapshotInput, digest
from packages.factors.fixtures import demo_inputs
from packages.rdagent_adapter.contracts import FactorResearchConfig
from packages.rdagent_adapter.host_evidence import research_definition
from packages.research_runtime.fences import AttemptFence
from runtimes.rdagent.probe_fixture import research_dict


def setup(monkeypatch):
    config = FactorResearchConfig.model_validate(research_dict())
    calendar, _ = demo_inputs()
    fence = AttemptFence(
        owner_id=UUID(int=1),
        run_id=UUID(int=2),
        attempt_id=UUID(int=3),
        generation=1,
        lease_token=UUID(int=4),
    )
    start = SimpleNamespace(id=UUID(int=5))
    source = SimpleNamespace(
        id=UUID(int=6),
        start_id=start.id,
        factor_version_id=UUID(int=7),
        source_sha256="a" * 64,
    )
    definition = research_definition(config, source.source_sha256)
    version = SimpleNamespace(
        id=source.factor_version_id,
        code_sha256=source.source_sha256,
        definition=definition.model_dump(mode="json"),
        definition_sha256=definition.sha256,
    )
    monkeypatch.setattr(
        domain, "require_research_spec", lambda *args: (object(), config)
    )
    monkeypatch.setattr(
        domain,
        "_scope_row",
        lambda session, model, *args, **kwargs: (
            start if model is FactorGenerationStart else source
        ),
    )
    monkeypatch.setattr(factors, "verify_version", lambda *args: None)
    monkeypatch.setattr(
        service,
        "local_exposure_history",
        lambda *args: ExposureHistory(completeness="unknown"),
    )
    return config, calendar, fence, start, source, version


def test_fresh_ticket_then_repeat_read_reuses_immutable_history(monkeypatch):
    config, calendar, fence, start, source, version = setup(monkeypatch)
    answers = iter([version, None, None, None])
    added = []
    session = SimpleNamespace(
        scalar=lambda query: next(answers),
        add=added.append,
        flush=lambda: None,
        get=lambda model, identity: SimpleNamespace(
            owner_id=fence.owner_id,
            content_sha256=config.input.dataset.content_sha256,
            start_date=config.input.window.start,
            end_date=config.input.window.end,
        ),
    )
    ticket = service.open_generation_evaluation(
        session,
        fence,
        start_id=start.id,
        source_id=source.id,
        calendar=calendar,
        settings=None,
    )
    assert len(added) == 1 and ticket.expected_cutoffs == 8
    snapshot = SnapshotInput.model_validate(ticket.snapshot_input)
    assert snapshot.materialization_profile == "mineagent.rdagent-isolated-prefix.v2"
    assert snapshot.environment_sha256 == digest(config.runtime)
    assert snapshot.exposure_history.has_unknown_history
    # A later exposure change does not rewrite this already admitted snapshot.
    monkeypatch.setattr(
        service,
        "local_exposure_history",
        lambda *args: pytest.fail("replay reread mutable exposure history"),
    )
    from backend.app import rdagent_boundary_services as boundary

    monkeypatch.setattr(
        boundary,
        "require_unobserved_generation",
        lambda *args: pytest.fail("replay must not reject observed generation"),
    )
    answers = iter([version, ticket])
    assert (
        service.open_generation_evaluation(
            session,
            fence,
            start_id=start.id,
            source_id=source.id,
            calendar=calendar,
            settings=None,
        )
        is ticket
    )
    assert len(added) == 1


@pytest.mark.parametrize(
    "bad", ["historical_source", "source_digest", "terminal", "calendar", "observed"]
)
def test_evaluation_admission_rejects_unverified_or_terminal_input(monkeypatch, bad):
    config, calendar, fence, start, source, version = setup(monkeypatch)
    if bad == "historical_source":
        source.start_id = UUID(int=99)
    elif bad == "source_digest":
        version.code_sha256 = "0" * 64
    elif bad == "calendar":
        raw = calendar.model_dump(mode="json")
        raw["sessions"] = raw["sessions"][:-1]
        calendar = type(calendar).model_validate(raw)
    answers = iter(
        [
            version,
            None,
            object() if bad == "terminal" else None,
            object() if bad == "observed" else None,
        ]
    )
    session = SimpleNamespace(
        scalar=lambda query: next(answers),
        add=lambda row: pytest.fail("invalid evaluation admitted"),
        flush=lambda: None,
        get=lambda model, identity: SimpleNamespace(
            owner_id=fence.owner_id,
            content_sha256=config.input.dataset.content_sha256,
            start_date=config.input.window.start,
            end_date=config.input.window.end,
        ),
    )
    with pytest.raises(DomainError):
        service.open_generation_evaluation(
            session,
            fence,
            start_id=start.id,
            source_id=source.id,
            calendar=calendar,
            settings=None,
        )
