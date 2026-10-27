"""Exact wire/ticket binding; this is not peer, fence or phase authority."""

import hashlib
import hmac
import json

from backend.app.openevolve_services import _owned_row
from backend.app.research_lifecycle import require_neutral_transaction
from backend.app.research_process_models import ResearchProcessAdmission
from packages.openevolve_adapter.observer_transport import canonical
from runtimes.openevolve_operational.wire import Event, parse_event


def exact_event(event):
    if type(event) is not Event:
        raise ValueError("Exact parsed operational event required")
    raw = canonical(
        {
            "event": json.loads(event.raw_event),
            "event_sha256": event.event_sha256,
            "process_ticket": event.process_ticket,
        }
    )
    if parse_event(raw) != event:
        raise ValueError("Operational event differs from canonical wire")
    return raw


def require_event_ticket(session, fence, event):
    require_neutral_transaction(session, fence)
    exact_event(event)
    admission = _owned_row(session, ResearchProcessAdmission, event.admission_id, fence)
    if not hmac.compare_digest(
        hashlib.sha256(event.process_ticket.encode()).hexdigest(),
        admission.ticket_sha256,
    ):
        raise ValueError("Operational event process ticket differs")
