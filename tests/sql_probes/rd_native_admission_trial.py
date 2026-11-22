"""Explicit test-only native trial patches; no runtime import or activation.

Only exact factories claimed inside this scope participate. Original transactions,
checks, deadlines, callbacks and pulse remain intact. No gate spans execution or
external I/O. A failed live-thread cleanup retains patches and fails red; run this
trial alone in its own pytest process, never proceed after that failure.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from threading import RLock

from sqlalchemy import text

from packages.research_runtime.fences import AttemptFence
from tests.sql_probes.rd_attempt_admission_candidate import AdmissionRejected
from tests.sql_probes.rd_ticketed_admission_candidate import TicketedAdmission

OWNER_LOCK_MAX_MS = 5000


def _lock_heartbeat_owner(session, factory, fence, ticket):
    """Bound only the additional lock, then run the original live fence."""
    from backend.app.services import advisory_lock

    if (
        not session.in_transaction()
        or session.in_nested_transaction()
        or session.get_bind() is not factory.kw["bind"]
    ):
        raise AdmissionRejected("heartbeat_session_mismatch")
    previous, previous_ms = session.execute(
        text(
            "SELECT current_setting('lock_timeout'), setting::integer "
            "FROM pg_settings WHERE name='lock_timeout'"
        )
    ).one()
    remaining_ms = int(ticket.remaining(factory, fence, role="heartbeat") * 1000)
    limit = min(remaining_ms, OWNER_LOCK_MAX_MS, previous_ms or OWNER_LOCK_MAX_MS)
    if limit <= 0:
        raise AdmissionRejected("expired")
    set_timeout = text("SELECT set_config('lock_timeout', :timeout, true)")
    session.execute(set_timeout, {"timeout": f"{limit}ms"})
    # Exact existing submit owner, before original heartbeat's Run/Attempt
    # locks. Do not patch or require a live lease for containment/recovery.
    advisory_lock(session, f"submit:{fence.owner_id}")
    session.execute(set_timeout, {"timeout": previous})
    ticket.remaining(factory, fence, role="heartbeat")


@contextmanager
def native_admission_trial():
    from backend.app import research_lifecycle as lifecycle
    from workers.research import lease as lease_module
    from workers.research import rdagent_queue
    from workers.research.lease import AttemptLease

    original_claim = rdagent_queue.claim_configured_run
    original_neutral = lifecycle._neutral_transaction_once
    original_pulse, original_close = AttemptLease.pulse, AttemptLease.close
    original_start = AttemptLease.start
    original_heartbeat = lease_module.heartbeat
    pulse_scope = ContextVar("native_trial_heartbeat", default=None)
    lock = RLock()
    entries = []
    leases = []
    counts = {
        "claims": 0,
        "neutral_commits": 0,
        "pulse_returns": 0,
        "owner_first_pulses": 0,
    }
    closing = False

    def entry(factory):
        with lock:
            return next((value for value in entries if value[0] is factory), None)

    def bind_lease(lease):
        value = entry(lease.factory)
        if value is None:
            return None
        _, fence, gate = value
        with lock:
            if not any(item is lease for item in leases):
                leases.append(lease)
        if (lease.attempt_id, lease.generation, lease.token) != (
            fence.attempt_id,
            fence.generation,
            fence.lease_token,
        ):
            raise AdmissionRejected("lease_binding_mismatch")
        return fence, gate

    def claim(factory, *args, **kwargs):
        with lock:
            if closing or entry(factory) is not None:
                raise AdmissionRejected("claim_scope_invalid")
        fence = original_claim(factory, *args, **kwargs)
        if fence is not None:
            gate = TicketedAdmission(factory, fence)
            with lock:
                if closing or entry(factory) is not None:
                    gate.close()
                    raise AdmissionRejected("claim_scope_invalid")
                entries.append((factory, fence, gate))
                counts["claims"] += 1
        return fence

    @contextmanager
    def neutral(factory, fence, *, resource_scope="none"):
        value = entry(factory)
        if lifecycle._neutral_scope.get() is not None or value is None:
            with original_neutral(
                factory, fence, resource_scope=resource_scope
            ) as session:
                yield session
            return
        if type(fence) is not AttemptFence or fence != value[1]:
            raise AdmissionRejected("binding_mismatch")
        with value[2].transaction(factory, fence, role="neutral"):
            with original_neutral(
                factory, fence, resource_scope=resource_scope
            ) as session:
                yield session
            with lock:
                counts["neutral_commits"] += 1

    def heartbeat(session, attempt_id, generation, token, settings):
        active = pulse_scope.get()
        if active is None:
            return original_heartbeat(session, attempt_id, generation, token, settings)
        factory, fence, ticket, observation = active
        if (attempt_id, generation, token) != (
            fence.attempt_id,
            fence.generation,
            fence.lease_token,
        ) or observation:
            raise AdmissionRejected("heartbeat_binding_mismatch")
        _lock_heartbeat_owner(session, factory, fence, ticket)
        result = original_heartbeat(session, attempt_id, generation, token, settings)
        observation.append(True)
        return result

    def pulse(lease, force=False):
        try:
            bound = bind_lease(lease)
            if bound is None:
                return original_pulse(lease, force=force)
            fence, gate = bound
            with gate.transaction(lease.factory, fence, role="heartbeat") as ticket:
                observation = []
                binding = pulse_scope.set((lease.factory, fence, ticket, observation))
                try:
                    result = original_pulse(lease, force=force)
                finally:
                    pulse_scope.reset(binding)
                with lock:
                    # Includes original throttled no-ops, not a renewal claim.
                    counts["pulse_returns"] += 1
                    counts["owner_first_pulses"] += bool(observation)
                return result
        except Exception:  # Original pulse failures revoke lease authority.
            lease.lost.set()
            lease.cancelled.set()
            raise

    def start(lease):
        # Register ownership before original start can launch a thread and then
        # fail/cancel. No new thread, interval, pulse, or wait is introduced.
        bound = bind_lease(lease)
        if bound is not None:
            with lock:
                if closing:
                    raise AdmissionRejected("trial_closing")
                return original_start(lease)
        return original_start(lease)

    def close(lease):
        bound = bind_lease(lease)
        if bound is not None:
            bound[1].close()
        original_close(lease)
        if bound is not None and lease.thread.is_alive():
            raise AdmissionRejected("lease_thread_still_alive")

    rdagent_queue.claim_configured_run = claim
    lifecycle._neutral_transaction_once = neutral
    AttemptLease.pulse, AttemptLease.close = pulse, close
    AttemptLease.start = start
    lease_module.heartbeat = heartbeat
    try:
        yield counts
    finally:
        with lock:
            closing = True
            owned_entries, owned_leases = list(entries), list(leases)
        for _, _, gate in owned_entries:
            gate.close()
        for lease in owned_leases:
            original_close(lease)
        if any(lease.thread.is_alive() for lease in owned_leases):
            # Do not restore functions underneath a live thread. This diagnostic
            # must be selected alone; its process termination is the final fence.
            raise AdmissionRejected("trial_cleanup_thread_alive")
        rdagent_queue.claim_configured_run = original_claim
        lifecycle._neutral_transaction_once = original_neutral
        AttemptLease.pulse, AttemptLease.close = original_pulse, original_close
        AttemptLease.start = original_start
        lease_module.heartbeat = original_heartbeat


def report_trial_counts(counts):
    """Best-effort closed bounded metrics; cannot replace native/cleanup failure."""
    try:
        if type(counts) is not dict:
            return
        output = {}
        for name in (
            "claims",
            "neutral_commits",
            "pulse_returns",
            "owner_first_pulses",
        ):
            value = counts.get(name)
            if type(value) is not int or value < 0:
                return
            output[name] = min(value, 1_000_000)
        print("RD_TICKETED_NATIVE_TRIAL", output)
    except BaseException:  # noqa: BLE001 -- diagnostics must never alter native outcome
        return
