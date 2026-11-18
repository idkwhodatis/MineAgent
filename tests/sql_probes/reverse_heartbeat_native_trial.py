"""Isolated native SQL experiment; never imported by production.

Restoration requires closed local clients, native absence and original cooperative
maintenance barriers. No thread/process is killed and no deadline is extended.
Run this case alone in a dedicated process and disposable PostgreSQL service.
"""

import re
import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import text

from packages.research_runtime.fences import AttemptFence
from tests.sql_probes import reverse_heartbeat_candidate as candidate

FILTERS = (
    "label=io.mineagent.managed=mineagent-oe-structured-orchestrator-v1",
    "label=io.mineagent.structured.admission",
    "label=io.mineagent.numeric.admission",
    "label=io.mineagent.analysis.admission",
    "label=io.mineagent.managed=mineagent-rdagent-orchestrator-v2",
    "name=^/mineagent-qlib-direct-installation-proof$",
)


def require_single_thread():
    if threading.current_thread() is not threading.main_thread() or any(
        item is not threading.main_thread() and item.is_alive()
        for item in threading.enumerate()
    ):
        raise AssertionError("Native SQL trial requires closed local clients")


def require_native_absence():
    for label in FILTERS:
        try:
            result = subprocess.run(
                ["docker", "ps", "--all", "--quiet", "--no-trunc", "--filter", label],
                capture_output=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            raise AssertionError(
                "Native SQL trial containment is unconfirmed"
            ) from None
        if result.returncode != 0 or result.stdout.strip():
            raise AssertionError("Native SQL trial containment is unconfirmed")


def require_only_connection(connection):
    count = connection.execute(
        text(
            "SELECT count(*) FROM pg_catalog.pg_stat_activity "
            "WHERE datname=current_database() AND pid<>pg_backend_pid()"
        )
    ).scalar_one()
    if count != 0:
        raise AssertionError("Native SQL trial has other database clients")


def retained_rows(connection):
    # Same bounded private row evidence as the completed-RD probe, collected
    # inside the exclusive restore transaction rather than another connection.
    tables = (
        connection.exec_driver_sql("""SELECT r.relname FROM pg_class r
      JOIN pg_namespace n ON n.oid=r.relnamespace
      WHERE n.nspname='public' AND r.relkind='r' ORDER BY r.relname""")
        .scalars()
        .all()
    )
    if not 0 < len(tables) <= 256:
        raise AssertionError("Native SQL row inventory bound exceeded")
    result = {}
    for table in tables:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", table):
            raise AssertionError("Native SQL table identity invalid")
        count, digest = connection.exec_driver_sql(f"""SELECT count(*),
          encode(sha256(convert_to(coalesce(jsonb_agg(value ORDER BY value::text)::text,'[]'),'UTF8')),'hex')
          FROM (SELECT to_jsonb(t) AS value FROM public."{table}" t LIMIT 16385) q""").one()
        if not 0 <= count <= 16384:
            raise AssertionError("Native SQL row inventory bound exceeded")
        result[table] = (count, digest)
    return result


def restore_quiescent(engine, storage):
    """Do not call until all owned Python clients have returned and stopped."""
    from backend.app.core.maintenance import _lock
    from backend.app.core.storage_barrier import storage_guard
    from backend.app.maintenance_process_quiescence import assert_maintenance_quiescent
    from runtimes.factor_parser.supervisor import assert_quiescent

    require_single_thread()
    require_native_absence()
    candidate.check(engine, candidate_enabled=True)
    # Explicit connection/transaction lifetime keeps root exclusion THROUGH commit.
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            _lock(connection, exclusive=True)
            with storage_guard(Path(storage), exclusive=True) as root_fd:
                assert_quiescent(storage)
                assert_maintenance_quiescent(storage, root_fd=root_fd)
                require_only_connection(connection)
                before = retained_rows(connection)
                connection.exec_driver_sql(candidate.render(False))
                if retained_rows(connection) != before:
                    raise AssertionError("Native SQL restore changed retained rows")
                require_only_connection(connection)
                transaction.commit()
        finally:
            if transaction.is_active:
                transaction.rollback()
    candidate.check(engine)


@contextmanager
def isolated_native_sql_trial(baseline, legacy, database_url, monkeypatch):
    """Instrument the unchanged baseline, not its worker transactions/pulses."""
    from workers.research import rdagent_queue
    from workers.research.lease import AttemptLease

    require_single_thread()
    require_native_absence()
    original_upgrade = legacy._upgrade
    original_environment = baseline.actual_environment
    original_start = AttemptLease.start
    original_claim = rdagent_queue.claim_configured_run
    claims = []
    state = {
        "installed": False,
        "environment_closed": False,
        "starts": 0,
        "restored": False,
        "engine": None,
        "factory": None,
        "storage": None,
    }
    leases = []

    def upgrade(engine, revision):
        result = original_upgrade(engine, revision)
        if engine.url != database_url:
            return result
        if revision != "032g_schema_keyset_cost" or state["installed"]:
            raise AssertionError("Exact isolated SQL upgrade scope required")
        candidate.check(engine)
        state["engine"] = engine
        with engine.begin() as connection:
            require_only_connection(connection)
            connection.exec_driver_sql(candidate.render(True))
        state["installed"] = True
        candidate.check(engine, candidate_enabled=True)
        return result

    @contextmanager
    def environment(engine, settings, patcher):
        if engine is not state["engine"] or not state["installed"]:
            raise AssertionError("Exact isolated native environment required")
        state["storage"] = Path(settings.storage_root)
        try:
            with original_environment(engine, settings, patcher) as env:
                state["factory"] = env.factory
                yield env
        finally:
            # Context exit may fail. The enclosing restoration still independently
            # requires no live threads, units, sessions or journal writers.
            state["environment_closed"] = True

    def owned_factory(factory):
        engine = getattr(factory, "kw", {}).get("bind")
        return engine is not None and engine.url == database_url

    def claim(factory, *args, **kwargs):
        result = original_claim(factory, *args, **kwargs)
        if owned_factory(factory) and result is not None:
            if type(result) is not AttemptFence:
                raise AssertionError("Original typed native SQL claim required")
            claims.append((factory, result))
        return result

    def start(lease):
        if owned_factory(lease.factory):
            # Real queue creates a separate engine/factory for this same owned DB.
            # Track BEFORE launch and require that exact factory's original claim.
            leases.append(lease)
            state["starts"] += 1
            binding = (lease.attempt_id, lease.generation, lease.token)
            if not any(
                factory is lease.factory
                and (fence.attempt_id, fence.generation, fence.lease_token) == binding
                for factory, fence in claims
            ):
                raise AssertionError("Native SQL lease claim binding unconfirmed")
        return original_start(lease)

    monkeypatch.setattr(legacy, "_upgrade", upgrade)
    monkeypatch.setattr(baseline, "actual_environment", environment)
    monkeypatch.setattr(AttemptLease, "start", start)
    monkeypatch.setattr(rdagent_queue, "claim_configured_run", claim)
    try:
        yield state
    finally:
        if state["installed"]:
            if not state["environment_closed"] or state["storage"] is None:
                raise AssertionError("Native SQL environment closure unconfirmed")
            if any(lease.thread.is_alive() for lease in leases):
                raise AssertionError("Native SQL lease thread remains live")
            require_single_thread()
            # Original baseline disposes its engine in finally; dispose again only
            # closes idle pooled clients, never checked-out sessions or threads.
            state["engine"].dispose()
            try:
                restore_quiescent(state["engine"], state["storage"])
                state["restored"] = True
            finally:
                state["engine"].dispose()


def verify_restored_completed_graph(engine):
    """Recheck the actual two successful children under original functions."""
    with engine.connect() as connection, connection.begin():
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        rows = connection.exec_driver_sql(
            "SELECT id,engine,status FROM research_runs ORDER BY engine LIMIT 3"
        ).all()
        if len(rows) != 2 or sorted((row.engine, row.status) for row in rows) != [
            ("openevolve", "succeeded"),
            ("rdagent", "succeeded"),
        ]:
            raise AssertionError("Exact completed native children required")
        roots = connection.exec_driver_sql(
            "SELECT id,owner_id,status FROM research_workflows ORDER BY status LIMIT 4"
        ).all()
        if len(roots) != 3 or [root.status for root in roots] != [
            "cancelled",
            "completed",
            "paused",
        ]:
            raise AssertionError(
                "Exact completed and control-only workflow roots required"
            )
        stages = connection.exec_driver_sql(
            "SELECT owner_id,workflow_id,kind,child_run_id FROM research_workflow_stages LIMIT 3"
        ).all()
        completed = next(root for root in roots if root.status == "completed")
        expected = {
            (
                "rd_factor_generation"
                if row.engine == "rdagent"
                else "structured_factor_execution",
                row.id,
            )
            for row in rows
        }
        if (
            len(stages) != 2
            or {(stage.kind, stage.child_run_id) for stage in stages} != expected
            or any(
                stage.workflow_id != completed.id
                or stage.owner_id != completed.owner_id
                for stage in stages
            )
        ):
            raise AssertionError("Exact original workflow child bindings required")
        for root in roots:
            if root.status != "completed":
                actions = (
                    connection.execute(
                        text(
                            "SELECT command_canonical::jsonb->>'action' FROM research_workflow_commands "
                            "WHERE workflow_id=:wid AND command_canonical::jsonb->>'action' IS NOT NULL LIMIT 2"
                        ),
                        {"wid": root.id},
                    )
                    .scalars()
                    .all()
                )
                if actions != (
                    ["cancel"] if root.status == "cancelled" else ["pause_after_stage"]
                ):
                    raise AssertionError("Exact control-only workflow command required")
            valid = connection.execute(
                text("SELECT public.mineagent_workflow_consistent(:owner,:wid)"),
                {"owner": root.owner_id, "wid": root.id},
            ).scalar_one()
            if valid is not True:
                raise AssertionError("Restored original workflow graph rejected")
        for row in rows:
            function = {
                "rdagent": "mineagent_rd_provider_graph",
                "openevolve": "mineagent_oes_graph_check",
            }[row.engine]
            connection.execute(
                text(f"SELECT public.{function}(:rid)"), {"rid": row.id}
            ).scalar_one()
