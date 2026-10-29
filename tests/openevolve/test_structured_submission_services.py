"""Normal queued persistence with explicit session doubles; no PG/outbox worker claim."""

from datetime import date
from types import SimpleNamespace as NS

from backend.app import oe_structured_submission_services as service
from backend.app.models import DatasetVersion, OutboxEvent, ResearchRun
from backend.app.oe_structured_models import (
    OEStructuredCandidate,
    OEStructuredExecution,
    OEStructuredSpec,
    OEStructuredSubmission,
)
from backend.app.oe_structured_provider_submission import (
    ProviderStructuredSearchRequest,
)
from backend.app.research_broker_models import ResearchBudgetControl
from backend.app.structured_factor_models import StructuredStrategyVersion
from tests.openevolve.test_structured_persisted_identity import graph


def test_normal_queue_has_user_seed_version_and_one_outbox_no_attempt_or_evaluation(
    monkeypatch,
):
    _, run, spec, _, _, _ = graph()
    import json

    from packages.openevolve_structured.provider_run import ProviderStructuredRunConfig

    config = ProviderStructuredRunConfig.model_validate_json(json.dumps(run.config))
    request = ProviderStructuredSearchRequest.model_validate_json(
        spec.request_canonical
    )
    dataset = DatasetVersion(
        id=config.fixed_inputs.dataset_version_id,
        owner_id=run.owner_id,
        content_sha256=config.fixed_inputs.dataset_sha256,
        start_date=date(2024, 3, 11),
        end_date=date(2024, 3, 15),
    )
    added = []
    session = NS(
        add=added.append,
        add_all=lambda values: added.extend(values),
        flush=lambda: None,
        get=lambda cls, key: dataset,
    )
    monkeypatch.setattr(service, "_identities", lambda *a: ["owned-fixture-identity"])
    checked = NS(preflight_sha256="f" * 64)
    data = NS(
        manifest=NS(input=NS(window=config.search.domain.seed_program.signal_window))
    )
    receipt = service._persist_queued(
        session,
        run.owner_id,
        "normal-key",
        request,
        checked,
        config.fixed_inputs,
        NS(runtime=config.runtime),
        config.model_binding,
        data,
    )
    queued = [row for row in added if isinstance(row, ResearchRun)]
    events = [row for row in added if isinstance(row, OutboxEvent)]
    assert len(queued) == len(events) == 1
    assert queued[0].status == "queued"
    assert queued[0].active_attempt_id is None
    assert queued[0].strategy_version_id is None
    assert queued[0].engine == "openevolve"
    assert events[0].run_id == receipt.run_id == queued[0].id
    assert not any(
        isinstance(row, (OEStructuredExecution, OEStructuredCandidate)) for row in added
    )
    version = next(row for row in added if isinstance(row, StructuredStrategyVersion))
    assert version.parent_version_id is None
    persisted = next(row for row in added if isinstance(row, OEStructuredSpec))
    submitted = next(row for row in added if isinstance(row, OEStructuredSubmission))
    assert persisted.seed_program_version_id == version.id
    assert persisted.config["search"]["execution_binding_id"] == str(persisted.run_id)
    assert persisted.submission_id == submitted.id == receipt.id
    assert submitted.origin == "authenticated_user_submission"
    budget = next(row for row in added if isinstance(row, ResearchBudgetControl))
    assert all(value == 0 for value in budget.used.values())
    assert all(value == 0 for value in budget.reserved.values())
