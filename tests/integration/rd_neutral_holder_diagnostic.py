"""One closed PostgreSQL lock snapshot on the first owned RD neutral rejection."""

import hashlib
import json
import os
import re
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
from uuid import UUID

from sqlalchemy import text

_EXPECTED = {
    "GITHUB_REPOSITORY": "idkwhodatis/MineAgent",
    "GITHUB_EVENT_NAME": "push",
    "GITHUB_REF": "refs/heads/chatgpt-dot/production-heartbeat-native-20261122",
    "DATA_PROVIDER_MODE": "fixture",
    "MODEL_PROFILES_ENABLED": "false",
    "E2E_LIVE_PROVIDERS": "0",
    "MINEAGENT_WORKFLOW_CURRENT_HEAD_REQUIRED": "1",
}
_DB = re.compile(r"tradegrad_backup_test_[0-9a-f]{32}\Z")
_HOLDERS = text("""
    SELECT pid FROM pg_locks
    WHERE locktype = 'advisory' AND granted
      AND database = (SELECT oid FROM pg_database WHERE datname=current_database())
      AND objsubid = 1
      AND classid::bigint = :hi AND objid::bigint = :lo
    LIMIT 2
""")
_FIELDS = ("owner_id", "run_id", "attempt_id", "generation", "lease_token")


def _key_parts(scope):
    key = int.from_bytes(hashlib.sha256(scope.encode()).digest()[:8], "big")
    return {"hi": key >> 32, "lo": key & 0xFFFFFFFF}


def _identity(values):
    return tuple(values[key] for key in _FIELDS)


def _role():
    frame = sys._getframe(1)
    names = set()
    for _ in range(24):
        if frame is None:
            break
        module = frame.f_globals.get("__name__")
        if module == "workers.research.rdagent_operational":
            names.add(frame.f_code.co_name)
        if (
            module == "runtimes.rdagent_operational.rpc"
            and frame.f_code.co_name == "serve_one"
        ):
            names.add("serve_one")
        frame = frame.f_back
    if "_recover_and_record" in names:
        return "recovery"
    if "stopped" in names:
        return "stop_callback"
    if "authorize" in names:
        return "rpc_auth"
    if "serve_one" in names:
        return "rpc_dispatch"
    if names & {"identity_observed", "child_observed", "before_submission"}:
        return "admission_callback"
    if "_execute_controller" in names:
        return "controller"
    return "other"


def _classify(pids, before, after, identity, observer_pid):
    if before != after or len(pids) != 1:
        return "unstable"
    pid = pids[0]
    if type(pid) is not int or pid <= 0 or pid == observer_pid:
        return "unobserved"
    return "workflow_heartbeat" if before[1].get(pid) == identity else "other"


def owned_current_fixture(engine, run_id):
    return (
        all(os.environ.get(k) == v for k, v in _EXPECTED.items())
        and engine.dialect.name == "postgresql"
        and _DB.fullmatch(engine.url.database or "") is not None
        and type(run_id) is UUID
    )


def owned_032h_fixture(engine, run_id):
    """The independently pinned revision may reuse masked errors, not lock sampling."""
    current_head = "MINEAGENT_WORKFLOW_CURRENT_HEAD_REQUIRED"
    if not (
        all(os.environ.get(k) == v for k, v in _EXPECTED.items() if k != current_head)
        and os.environ.get(current_head) != "1"
        and os.environ.get("MINEAGENT_WORKFLOW_032H_REQUIRED") == "1"
        and os.environ.get("MINEAGENT_NATIVE_PROOF_SCOPE") == "current-032h"
        and engine.dialect.name == "postgresql"
        and _DB.fullmatch(engine.url.database or "") is not None
        and type(run_id) is UUID
    ):
        return False
    with engine.connect() as connection, connection.begin():
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        connection.exec_driver_sql("SET LOCAL statement_timeout = '1s'")
        return connection.exec_driver_sql(
            "SELECT version_num FROM public.alembic_version LIMIT 2"
        ).all() == [("032h_workflow_oe_run_guard",)]


@contextmanager
def observe_first_neutral_holder(engine, run_id):
    if not owned_current_fixture(engine, run_id):
        yield
        return

    from backend.app import research_lifecycle as lifecycle
    from backend.app.services import DomainError
    from workers.research import rdagent_workflow_lease as leases

    original_transaction = lifecycle._neutral_transaction_once
    original_try = lifecycle._try_neutral_lock
    original_pulse = leases.WorkflowAttemptLease._pulse_transaction
    original_advisory = leases.advisory_lock
    neutral = ContextVar("rd_neutral_holder_neutral", default=None)
    pulse = ContextVar("rd_neutral_holder_pulse", default=None)
    mutex = Lock()
    live = {}
    version = 0
    uncertain = False
    observed = None

    def snapshot():
        with mutex:
            return (version, uncertain), dict(live)

    @contextmanager
    def transaction(factory, fence, *, resource_scope="none"):
        token = neutral.set(fence if getattr(fence, "run_id", None) == run_id else None)
        try:
            with original_transaction(
                factory, fence, resource_scope=resource_scope
            ) as session:
                yield session
        finally:
            neutral.reset(token)

    @contextmanager
    def heartbeat(lease):
        nonlocal version
        # Record the connection before acquisition; pg_locks alone attests it
        # actually held the key. Remove registration after original rollback/commit.
        data = [lease, None] if lease._params.get("run_id") == run_id else None
        token = pulse.set(data)
        try:
            with original_pulse(lease) as session:
                yield session
        finally:
            if data is not None and data[1] is not None:
                with mutex:
                    live.pop(data[1], None)
                    version += 1
            pulse.reset(token)

    def advisory(session, scope):
        nonlocal version, uncertain
        data = pulse.get()
        if data is not None and data[1] is None and scope == data[0]._owner_scope:
            try:
                connection = session.connection()
                pid = connection.connection.driver_connection.info.backend_pid
                if (
                    connection.engine.url.database == engine.url.database
                    and type(pid) is int
                    and pid > 0
                ):
                    with mutex:
                        live[pid] = _identity(data[0]._params)
                        data[1] = pid
                        version += 1
            except Exception:  # noqa: BLE001, S110 - never replace original lock
                pass
            if data[1] is None:
                with mutex:
                    uncertain = True
                    version += 1
        return original_advisory(session, scope)

    def try_lock(session, scope):
        nonlocal observed
        try:
            return original_try(session, scope)
        except DomainError as error:
            fence = neutral.get()
            if error.code == "neutral_busy" and fence is not None and observed is None:
                with mutex:
                    sample = observed is None
                    if sample:
                        observed = {
                            "lock_scope": "unobserved",
                            "holder": "unobserved",
                            "caller": "other",
                        }
                if sample:
                    try:
                        connection = session.connection()
                        if connection.engine.url.database == engine.url.database:
                            lock_scope = (
                                "owner"
                                if scope == f"submit:{fence.owner_id}"
                                else "resource"
                            )
                            before = snapshot()
                            pids = (
                                session.execute(_HOLDERS, _key_parts(scope))
                                .scalars()
                                .all()
                            )
                            holder = _classify(
                                pids,
                                before,
                                snapshot(),
                                tuple(getattr(fence, key) for key in _FIELDS),
                                connection.connection.driver_connection.info.backend_pid,
                            )
                            if before[0][1]:
                                holder = "unobserved"
                            observed = {
                                "lock_scope": lock_scope,
                                "holder": holder,
                                "caller": _role(),
                            }
                    except Exception:  # noqa: BLE001, S110 - never replace original rejection
                        pass
            raise

    lifecycle._neutral_transaction_once = transaction
    lifecycle._try_neutral_lock = try_lock
    leases.WorkflowAttemptLease._pulse_transaction = heartbeat
    leases.advisory_lock = advisory
    try:
        yield
    finally:
        lifecycle._neutral_transaction_once = original_transaction
        lifecycle._try_neutral_lock = original_try
        leases.WorkflowAttemptLease._pulse_transaction = original_pulse
        leases.advisory_lock = original_advisory
        if observed is not None:
            try:
                print(
                    "RD_NEUTRAL_HOLDER_V1:" + json.dumps(observed, sort_keys=True),
                    flush=True,
                )
            except Exception:  # noqa: BLE001, S110 - preserve fixture outcome
                pass
