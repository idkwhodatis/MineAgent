"""Explicit transaction-boundary runner for the real deterministic input stage.

This returns an actual derivation receipt, not a workflow checkpoint or a claim
that a downstream RD child ran. The workflow dispatcher independently reconciles
that committed receipt under its root CAS before freezing/submitting next inputs.
No external network, provider, child process, or arbitrary callback is invoked.
"""

from dataclasses import dataclass
from uuid import UUID

from backend.app.core.maintenance import shared_lifecycle
from backend.app.factor_services import publication
from backend.app.research_input_derivations import (
    events_for,
    finish_external,
    prepare_external,
    start_external,
)
from backend.app.research_input_storage import (
    publish_input_object,
    require_input_capability,
    verify_payload,
)
from backend.app.services import DomainError
from packages.research_inputs.normalization import MAX_OUTPUT_BYTES

PUBLICATION_PEAK_BYTES = MAX_OUTPUT_BYTES + 32768
PUBLICATION_PEAK_FILES = 33


def run_external_stage(factory, settings, *, owner_id, workflow_id, stage_id):
    """Prepare/start/terminal commits are intentional, visible workflow phases.

    A crash after start leaves started_no_terminal_receipt, never false success.
    A second runner cannot adopt that start. Recovery must append an explicitly
    guarded interrupted receipt/new stage; it must not re-use this attempt.
    """
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        factory() as session,
    ):
        derivation, _prepared = prepare_external(
            session, owner_id, workflow_id, stage_id, settings
        )
        identity = derivation.id
        prior = events_for(session, derivation)
        if prior[-1].state not in {"prepared", "started"}:
            # Exact committed receipt only; no regenerated outputs or fresh ACK.
            result = prior[-1]
            session.rollback()
            return result
        if len(prior) != 1:
            raise DomainError(
                "input_stage_already_started",
                "Input stage already started and requires history or recovery",
                409,
            )
        session.commit()
        started = start_external(session, owner_id, workflow_id, stage_id, identity)
        session.commit()
        if started.state == "cancelled":
            # Prepared-only cancellation did not normalize or publish outputs.
            # The root observer independently verifies this exact terminal graph.
            return started
        # publication() deliberately clears preliminary read transactions before
        # taking existing owner/install/physical fences. Never nest it inside a
        # factory.begin() context which its rollback would invalidate.
        with publication(
            session,
            owner_id,
            settings,
            peak_bytes=PUBLICATION_PEAK_BYTES,
            peak_files=PUBLICATION_PEAK_FILES,
        ) as admission:
            terminal = finish_external(
                session,
                owner_id,
                workflow_id,
                stage_id,
                identity,
                started.id,
                settings,
                admission=admission,
            )
        # publication owns the bounded commit and post-commit deadline check.
        # Do not create a second logical transaction after its guarded commit.
        return terminal


@dataclass(frozen=True)
class RetainedExternalObject:
    id: UUID
    sha256: str
    size_bytes: int


def retain_external_object(factory, settings, *, owner_id, raw):
    """Bounded passive intake; malformed/empty JSON remains retained raw evidence.

    HTTP streaming must stop at64KiB too. This final boundary never truncates,
    normalizes, fetches URLs, creates a stage, or returns raw content. A retry of
    identical owned bytes returns the same persisted content-addressed object.
    """
    verify_payload("raw_external", raw)
    with (
        shared_lifecycle(factory.kw["bind"], settings.storage_root),
        factory() as session,
    ):
        require_input_capability(session)
        with publication(
            session, owner_id, settings, peak_bytes=2 * len(raw), peak_files=2
        ) as admission:
            row = publish_input_object(
                session, owner_id, "raw_external", raw, settings, admission=admission
            )
            result = RetainedExternalObject(
                id=row.id, sha256=row.sha256, size_bytes=row.size_bytes
            )
        # publication already committed under its original deadline.
        return result
