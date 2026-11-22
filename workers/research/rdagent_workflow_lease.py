"""Bounded owner-first heartbeat for committed v3 RD workflow claims."""

import math
import time
from contextlib import contextmanager
from time import monotonic

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from backend.app.models import WorkerHeartbeat, utcnow
from backend.app.rdagent_workflow_coordination import OWNER_WAIT_SECONDS, heartbeat
from backend.app.services import FenceLost, advisory_lock
from packages.research_runtime.fences import AttemptFence
from workers.research import lease as lease_module
from workers.research.lease import AttemptLease

_MAX_OWNER_WAIT_MS = int(OWNER_WAIT_SECONDS * 1000)
_IDENTITY = """
 FROM execution_attempts a JOIN research_runs r ON r.id=a.run_id
 WHERE r.owner_id=:owner_id AND r.id=:run_id
 AND r.active_attempt_id=:attempt_id AND r.generation=:generation
 AND a.id=:attempt_id AND a.generation=:generation
 AND a.lease_token=:lease_token AND a.worker_id=:worker_id
 AND a.finished_at IS NULL
 AND r.purpose='factor_research' AND r.engine='rdagent'
 AND r.status NOT IN ('succeeded','failed','cancelled','interrupted','paused')"""
_OBSERVE = text(
    "SELECT EXTRACT(EPOCH FROM (a.lease_expires_at - clock_timestamp()))"
    + _IDENTITY
    + """
 AND r.config->>'schema_version' = 'mineagent.factor-research.v3'
 AND r.config->>'workflow_child_run_id' = r.id::text
 AND EXISTS (
     SELECT 1 FROM factor_workflow_submissions s
     WHERE s.owner_id=r.owner_id AND s.run_id=r.id
     AND s.submission_origin='workflow_stage'
 )"""
)
_TIMEOUT = text(
    "SELECT current_setting('lock_timeout'), setting::integer"
    " FROM pg_settings WHERE name='lock_timeout'"
)
_SET_TIMEOUT = text("SELECT set_config('lock_timeout', :timeout, true)")


def _parameters(fence, worker_id):
    if type(fence) is not AttemptFence or type(worker_id) is not str or not worker_id:
        raise FenceLost()
    return {
        "owner_id": fence.owner_id,
        "run_id": fence.run_id,
        "attempt_id": fence.attempt_id,
        "generation": fence.generation,
        "lease_token": fence.lease_token,
        "worker_id": worker_id,
    }


def _read_committed(factory, query, params):
    try:
        with factory() as session:
            # Configure before after_begin acquires the shared maintenance guard.
            session.connection(
                execution_options={
                    "isolation_level": "READ COMMITTED",
                    "postgresql_readonly": True,
                }
            )
            return session.execute(query, params).scalar_one_or_none()
    except SQLAlchemyError:
        # Do not disclose the bound lease token through driver error text.
        raise FenceLost() from None


def configured_rd_lease(factory, settings, fence, worker_id, *, workflow):
    """Use the mode reported by the already committed configured claim."""
    if workflow is True:
        return WorkflowAttemptLease(factory, settings, fence, worker_id)
    if workflow is not False:
        raise FenceLost()
    return AttemptLease(
        factory,
        settings,
        fence.attempt_id,
        fence.generation,
        fence.lease_token,
        worker_id,
    )


class WorkflowAttemptLease(AttemptLease):
    def __init__(self, factory, settings, fence, worker_id):
        self._params = _parameters(fence, worker_id)
        self._owner_scope = f"submit:{fence.owner_id}"
        self._fence = fence
        super().__init__(
            factory,
            settings,
            fence.attempt_id,
            fence.generation,
            fence.lease_token,
            worker_id,
        )

    @contextmanager
    def _pulse_transaction(self):
        started = monotonic()
        seconds = _read_committed(self.factory, _OBSERVE, self._params)
        if seconds is None or not math.isfinite(seconds) or seconds <= 0:
            raise FenceLost()
        # Anchor at read start, so network/connection time can only reduce wait.
        cutoff = started + float(seconds)
        # The configured worker factory uses a 5s PostgreSQL lock timeout.
        # Local admission and the SQL wait share that single budget. A stricter
        # per-session lock_timeout still bounds the SQL wait below.
        owner_cutoff = min(cutoff, started + OWNER_WAIT_SECONDS)
        with heartbeat(self.factory, self._fence, deadline=owner_cutoff):
            if self.done.is_set():
                raise FenceLost()
            with self.factory.begin() as session:
                self._lock_owner(session, owner_cutoff)
                yield session

    def _lock_owner(self, session, cutoff):
        original, original_ms = session.execute(_TIMEOUT).one()
        left = cutoff - monotonic()
        if left <= 0:
            raise FenceLost()
        bound_ms = min(
            int(left * 1000), original_ms or _MAX_OWNER_WAIT_MS, _MAX_OWNER_WAIT_MS
        )
        if bound_ms <= 0:
            raise FenceLost()
        session.execute(_SET_TIMEOUT, {"timeout": f"{bound_ms}ms"})
        advisory_lock(session, self._owner_scope)
        session.execute(_SET_TIMEOUT, {"timeout": original})
        if monotonic() >= cutoff:
            raise FenceLost()
        # The committed observation is only a ceiling; the unchanged
        # heartbeat now takes Run -> Attempt and rechecks the live fence.

    def pulse(self, force=False):
        # Generic leases are a separately pinned legacy runtime input. Retain
        # their pulse contract here without changing their implementation.
        with self.lock:
            if not force and time.monotonic() - self.last_pulse < 1:
                return
            try:
                with self._pulse_transaction() as session:
                    if lease_module.heartbeat(
                        session,
                        self.attempt_id,
                        self.generation,
                        self.token,
                        self.settings,
                    ):
                        self.cancelled.set()
                    session.execute(
                        insert(WorkerHeartbeat)
                        .values(
                            worker_id=self.worker_id,
                            role="research",
                            heartbeat_at=utcnow(),
                        )
                        .on_conflict_do_update(
                            index_elements=[WorkerHeartbeat.worker_id],
                            set_={"heartbeat_at": utcnow()},
                        )
                    )
                self.last_pulse = time.monotonic()
            except Exception:
                self.lost.set()
                self.cancelled.set()
                raise
