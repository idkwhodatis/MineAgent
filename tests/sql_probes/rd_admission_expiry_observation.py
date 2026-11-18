"""Isolated scheduling observations, NOT live fencing or runtime integration.

Each call reads once in its own READ COMMITTED, READ ONLY transaction. A ticket
never refreshes its cutoff. Later calls may observe a real committed renewal.
Consumers must retain original locked fencing and phase-specific observers;
this module does not assign controller/SDK deadlines or authorize cleanup.
The default neutral role keeps its original status predicates. Heartbeat tickets
permit original cancellation/pause-request handling, never authorize neutral work.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime
from time import monotonic

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from packages.research_runtime.fences import AttemptFence
from tests.sql_probes.rd_attempt_admission_candidate import AdmissionRejected

EXPIRY_QUERY = """SELECT a.lease_expires_at, clock_timestamp() AS observed_at
 FROM execution_attempts a JOIN research_runs r ON r.id=a.run_id
 WHERE r.owner_id=:owner_id AND r.id=:run_id
 AND r.active_attempt_id=:attempt_id AND r.generation=:generation
 AND a.id=:attempt_id AND a.run_id=:run_id AND a.generation=:generation
 AND a.lease_token=:lease_token AND a.finished_at IS NULL
 AND r.status IN ('preparing','running','finalizing')
 AND a.status IN ('preparing','running','finalizing')
 AND r.purpose='factor_research' AND r.engine='rdagent'
"""


# Original fenced_attempt does not require a running attempt status and permits
# pause_requested/cancel_requested runs. Terminal paused remains rejected.
# Full factory/fence binding is an additional scheduling scope, not authority.
HEARTBEAT_EXPIRY_QUERY = """SELECT a.lease_expires_at, clock_timestamp() AS observed_at
 FROM execution_attempts a JOIN research_runs r ON r.id=a.run_id
 WHERE r.owner_id=:owner_id AND r.id=:run_id
 AND r.active_attempt_id=:attempt_id AND r.generation=:generation
 AND a.id=:attempt_id AND a.run_id=:run_id AND a.generation=:generation
 AND a.lease_token=:lease_token AND a.finished_at IS NULL
 AND r.status NOT IN ('succeeded','failed','cancelled','interrupted','paused')
"""


def _time(clock):
    value = clock()
    if type(value) not in (int, float) or not math.isfinite(value):
        raise AdmissionRejected("invalid_clock")
    return value


@dataclass(frozen=True, slots=True, repr=False)
class ExpiryTicket:
    """Immutable scheduling ceiling only; contains no authority cache."""

    _factory: object = field(repr=False)
    _fence: AttemptFence = field(repr=False)
    started: float
    cutoff: float
    role: str = "neutral"

    def remaining(self, factory, fence, *, role="neutral", clock=monotonic):
        if (
            factory is not self._factory
            or type(fence) is not AttemptFence
            or fence != self._fence
        ):
            raise AdmissionRejected("binding_mismatch")
        if type(role) is not str or role != self.role:
            raise AdmissionRejected("role_mismatch")
        try:
            now = _time(clock)
        except Exception:  # noqa: BLE001 -- suppress private clock or database details
            raise AdmissionRejected("invalid_clock") from None
        if now < self.started:
            raise AdmissionRejected("invalid_clock")
        remaining = self.cutoff - now
        if remaining <= 0:
            raise AdmissionRejected("expired")
        return remaining


class CommittedExpiryObserver:
    def __init__(self, factory, fence):
        if not isinstance(factory, sessionmaker) or type(fence) is not AttemptFence:
            raise AdmissionRejected("invalid_binding")
        self._factory, self._fence = factory, fence

    def observe(self, factory, fence, *, role="neutral", clock=monotonic):
        if (
            factory is not self._factory
            or type(fence) is not AttemptFence
            or fence != self._fence
        ):
            raise AdmissionRejected("binding_mismatch")
        if type(role) is not str or role not in {"neutral", "heartbeat"}:
            raise AdmissionRejected("invalid_role")
        query = EXPIRY_QUERY if role == "neutral" else HEARTBEAT_EXPIRY_QUERY
        try:
            started = _time(clock)
            with factory() as session:
                # Configure BEFORE Session.after_begin's original maintenance
                # guard executes SQL. SET TRANSACTION after that SELECT is too
                # late to choose isolation. Keep that shared maintenance guard.
                session.connection(
                    execution_options={
                        "isolation_level": "READ COMMITTED",
                        "postgresql_readonly": True,
                    }
                )
                row = session.execute(
                    text(query),
                    {
                        "owner_id": fence.owner_id,
                        "run_id": fence.run_id,
                        "attempt_id": fence.attempt_id,
                        "generation": fence.generation,
                        "lease_token": fence.lease_token,
                    },
                ).one_or_none()
            if row is None:
                raise AdmissionRejected("observation_rejected")
            expiry, observed = row
            if any(
                type(v) is not datetime or v.utcoffset() is None
                for v in (expiry, observed)
            ):
                raise AdmissionRejected("observation_rejected")
            duration = (expiry - observed).total_seconds()
            cutoff = started + duration
            if duration <= 0 or not math.isfinite(cutoff):
                raise AdmissionRejected("observation_rejected")
            ticket = ExpiryTicket(factory, fence, started, cutoff, role)
            ticket.remaining(factory, fence, role=role, clock=clock)
            return ticket
        except Exception:  # noqa: BLE001 -- suppress private clock or database details
            # SQL exceptions can contain full bound fence/token parameters.
            raise AdmissionRejected("observation_rejected") from None
