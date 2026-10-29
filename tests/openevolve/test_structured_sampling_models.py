"""ORM link construction, not installed PostgreSQL semantic acceptance."""

from pathlib import Path

from sqlalchemy import UniqueConstraint

from backend.app.oe_structured_models import SCOPE
from backend.app.oe_structured_operational_models import OEStructuredIterationIntent
from backend.app.oe_structured_sampling_models import (
    OEStructuredSamplingIntent,
    OEStructuredWorkerPlacement,
)


def test_completed_intent_requires_both_actual_prior_stages():
    links = {
        (
            tuple(item.parent.name for item in link.elements),
            tuple(item.column.table.name for item in link.elements),
            tuple(item.column.name for item in link.elements),
        )
        for link in OEStructuredIterationIntent.__table__.foreign_key_constraints
    }
    names = (*SCOPE, "id")
    assert (names, ("oe_structured_sampling_intents",) * len(names), names) in links
    assert (
        names,
        ("oe_structured_worker_placements",) * len(names),
        (*SCOPE, "sampling_intent_id"),
    ) in links
    assert any(
        isinstance(rule, UniqueConstraint)
        and tuple(rule.columns.keys()) == (*SCOPE, "sampling_intent_id")
        for rule in OEStructuredWorkerPlacement.__table__.constraints
    )


def test_partial_sampling_has_no_placeholder_render_or_kernel_identity():
    columns = set(OEStructuredSamplingIntent.__table__.columns.keys())
    assert {
        "mutation_reservation_id",
        "sampling_state_id",
        "selection_sha256",
        "worker_ticket_sha256",
    } <= columns
    assert (
        not {"rendered_canonical", "context_canonical", "worker_identity_canonical"}
        & columns
    )


def test_host_only_new_validators_do_not_expand_sealed_fixture_image():
    root = Path(__file__).resolve().parents[2]
    rules = (
        (root / "runtimes/openevolve_structured/Dockerfile.dockerignore")
        .read_text()
        .splitlines()
    )
    assert "packages/openevolve_structured/provider_*.py" in rules
    assert "packages/openevolve_structured/upload_contracts.py" in rules
    assert "packages/openevolve_structured/checkpoint_object_identity.py" in rules
    assert "packages/openevolve_structured/host_events.py" in rules
    assert "packages/openevolve_structured/sampling_validation.py" in rules


def test_epoch_admission_cycle_is_exactly_scoped_and_commit_deferred():
    from backend.app.oe_structured_operational_models import OEStructuredEpoch

    links = [
        link
        for link in OEStructuredEpoch.__table__.foreign_key_constraints
        if tuple(link.column_keys) == (*SCOPE, "controller_admission_id")
    ]
    assert len(links) == 1
    link = links[0]
    assert tuple(item.target_fullname for item in link.elements) == tuple(
        f"research_process_admissions.{key}" for key in (*SCOPE, "id")
    )
    assert link.deferrable is True and link.initially == "DEFERRED"
