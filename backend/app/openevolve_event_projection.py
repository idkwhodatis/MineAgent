"""Unregistered controller projection boundary, not an event server/dispatcher.

Each call repeats the existing live kernel + durable launch receipt check inside
one current neutral transaction. Return values are data, never reusable permits.
No object writes, reservation allocation, service mutation or ACK occurs here.
"""

import hashlib
import hmac

from backend.app.engine_peer_services import ControllerBinding, require_controller_peer
from backend.app.openevolve_services import _locked_epoch
from backend.app.research_lifecycle import require_neutral_transaction
from backend.app.research_process_models import ResearchProcessAdmission
from backend.app.services import DomainError
from packages.openevolve_adapter.event_projection import parse_controller_event


def inspect_authenticated_controller_event(
    session, fence, authorization, wire, *, expected_process_profile
):
    """expected_process_profile is fixed host configuration, never request content.

    The actual dispatcher must perform mutation in this SAME transaction after
    scoped intent/evidence resolution. It must not reuse the result for another
    event or send an ACK until the encompassing transaction has committed.
    """
    require_neutral_transaction(session, fence)
    parsed = parse_controller_event(wire)
    epoch = _locked_epoch(session, fence, parsed.event.epoch_id)
    admission = session.get(ResearchProcessAdmission, epoch.admission_id)
    if admission is None or parsed.event.admission_id != epoch.admission_id:
        raise DomainError(
            "search_process", "Event differs from exact epoch admission", 409
        )
    from runtimes.openevolve_operational.profile import PROFILE

    if expected_process_profile == PROFILE:
        from backend.app.openevolve_controller_services import require_search_controller

        _launch, exact_epoch = require_search_controller(
            session, fence, authorization, epoch_id=epoch.id
        )
        if exact_epoch.id != epoch.id or not hmac.compare_digest(
            hashlib.sha256(parsed.process_ticket.encode()).hexdigest(),
            admission.ticket_sha256,
        ):
            raise DomainError(
                "search_process", "Protected controller ticket differs", 409
            )
        return parsed
    # Shared resolver uses the persisted launch operation, never event context IDs.
    running = require_controller_peer(
        session,
        fence,
        authorization,
        binding=ControllerBinding(
            admission_id=epoch.admission_id,
            role="openevolve_orchestrator",
            profile=expected_process_profile,
            image_id=admission.image_id,
            bootstrap_sha256=epoch.bootstrap_sha256,
        ),
    )
    if (
        running.id != epoch.admission_id
        or running.profile != expected_process_profile
        or not hmac.compare_digest(
            hashlib.sha256(parsed.process_ticket.encode()).hexdigest(),
            running.ticket_sha256,
        )
    ):
        raise DomainError(
            "search_process",
            "Controller requires exact authenticated leader and profile",
            409,
        )
    # _locked_epoch verifies owner/run/attempt/generation, fixed runtime profile,
    # immutable image/bootstrap, spec and resolved execution profile. It still
    # fails closed under today's unregistered OE identity/profile.
    return parsed
