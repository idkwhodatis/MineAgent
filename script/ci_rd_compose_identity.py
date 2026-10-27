"""Read-only hosted Compose identity proof; never a launcher or admission issuer.

Only bounded booleans leave the disposable worker. Kernel peer fields come from
production accept_controller's actual SO_PEERCRED receipt, not fixture claims.
"""

import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from packages.research_runtime.process_contracts import ProcessError, canonical

RESULT = Path("/tmp/mineagent-rd-compose/identity-observed.json")


def require_boundary(value, *, host_pid, candidate=False):
    host = value["HostConfig"]
    if (
        host.get("PidMode", "") != ("host" if host_pid else "")
        or host.get("Privileged") is not False
        or host.get("ReadonlyRootfs") is not True
        or set(host.get("CapAdd") or [])
        != ({"SETUID", "SETGID", "KILL"} if candidate else set())
        or "ALL" not in host.get("CapDrop", [])
        or not any(
            v in ("no-new-privileges", "no-new-privileges:true")
            for v in host.get("SecurityOpt", [])
        )
        or any(
            v in ("seccomp=unconfined", "apparmor=unconfined")
            for v in host.get("SecurityOpt", [])
        )
        or host.get("NetworkMode") == "host"
        or (candidate and host.get("NetworkMode") != "none")
        or any(
            m.get("Source") in ("/", "/proc")
            or m.get("Destination") in ("/host", "/proc")
            for m in value.get("Mounts", [])
        )
    ):
        raise ValueError("Compose isolation boundary differs")
    if not candidate:
        user = value["Config"].get("User", "")
        if not user or user.split(":")[0] in ("0", "root"):
            raise ValueError("Trusted application must remain nonroot")
        if not host_pid and any(
            m.get("Destination") == "/var/run/docker.sock"
            for m in value.get("Mounts", [])
        ):
            raise ValueError("Private application has daemon access")


def applications(ids):
    from runtimes.research_supervisor.numeric_supervisor import NumericDockerClient

    if len(ids) != 4 or len(set(ids)) != 4:
        raise ValueError("Four exact services required")
    client = NumericDockerClient()
    client.deadline = time.monotonic() + 30
    for index, cid in enumerate(ids):
        if len(cid) != 64 or any(c not in "0123456789abcdef" for c in cid):
            raise ValueError("Exact container ID required")
        value = client._inspect(cid)
        if value is None or value["Id"] != cid:
            raise ValueError("Application container missing")
        require_boundary(value, host_pid=index >= 2)


def receipt_matches(record, row):
    payload = record.model_dump(mode="json")
    if (
        row.payload != payload
        or row.payload_sha256 != hashlib.sha256(canonical(payload)).hexdigest()
    ):
        raise ValueError("Retained identity receipt differs")


def original_deadline(not_after):
    remaining = (not_after - datetime.now(UTC)).total_seconds()
    if remaining <= 0:
        raise ValueError("Original native deadline elapsed")
    return time.monotonic() + remaining


def controller(root, admission, receipts):
    from runtimes.rdagent_operational.daemon_identity import DaemonIdentityContext
    from runtimes.rdagent_operational.process_journal import read_controller_journal

    launch, identity, child, _ = read_controller_journal(
        root, admission, with_child=True
    )
    receipt_matches(identity, receipts["launch_identity"])
    receipt_matches(child, receipts["controller_child"])
    context = DaemonIdentityContext(
        identity.container_id,
        identity.image_id,
        identity.leader.host_pid,
        original_deadline(launch.intent.not_after),
    )
    leader, observed = context.observe_pair(child.peer_pid)
    if (
        leader != identity.leader
        or observed != child.controller
        or child.peer_pid != observed.host_pid
        or (child.peer_uid, child.peer_gid) != (65533, 65533)
    ):
        raise ValueError("Actual kernel peer/start ticks/container differs")
    # This live host PID exists, but is not a member of the exact native unit.
    try:
        context.observe(os.getpid())
    except ProcessError:
        pass
    else:
        raise ValueError("Unrelated host PID accepted")
    # A timeout/disappeared unit must not masquerade as a successful negative.
    if context.observe_pair(child.peer_pid) != (leader, observed):
        raise ValueError("Native identity changed during negative observation")
    return True


def require_candidate_status(raw):
    if not 0 < len(raw) <= 65536:
        raise ValueError("Candidate status bound")
    status = dict(line.split(":", 1) for line in raw.decode("ascii").splitlines())
    if (
        tuple(map(int, status["Uid"].split())) != (65533,) * 4
        or tuple(map(int, status["Gid"].split())) != (65533,) * 4
        or any(
            int(status[k], 16) != 0 for k in ("CapEff", "CapPrm", "CapInh", "CapAmb")
        )
        or int(status["NoNewPrivs"]) != 1
    ):
        raise ValueError("Generated candidate privilege drop differs")


def candidate_child(client, identity):
    from runtimes.rdagent_operational.daemon_identity import DaemonIdentityContext
    from runtimes.research_supervisor.numeric_daemon_identity import (
        TOP_FIELDS,
        parse_top,
    )

    context = DaemonIdentityContext(
        identity.container_id, identity.image_id, identity.init_pid, client.deadline
    )
    while time.monotonic() < client.deadline:
        _, raw = client._command(["top", identity.container_id, "-eo", TOP_FIELDS])
        table = parse_top(raw)
        children = [
            pid
            for pid, row in table.items()
            if row[0] == 65533 and row[1] == identity.init_pid
        ]
        if len(children) > 1:
            raise ValueError("Ambiguous generated child")
        if children:
            pid = children[0]
            before = context.observe(pid)
            if table[pid][2] != identity.pid_namespace_inode:
                raise ValueError("Generated child namespace differs")
            with (Path("/proc") / str(pid) / "status").open("rb") as stream:
                require_candidate_status(stream.read(65537))
            if context.observe(pid) != before:
                raise ValueError("Generated child changed during observation")
            return
        time.sleep(0.01)
    raise ValueError("Generated child privilege drop not observed")


def candidate(root, admission, receipts):
    from packages.research_runtime.numeric_process_journal import (
        NumericIdentity,
        NumericLaunch,
    )
    from runtimes.research_supervisor.numeric_daemon_identity import observe_leader
    from runtimes.research_supervisor.numeric_journal import read_numeric_record
    from runtimes.research_supervisor.numeric_supervisor import NumericDockerClient
    from runtimes.research_supervisor.supervisor import operation_path

    directory = operation_path(root, admission) / "control"
    launch = NumericLaunch.model_validate_json(
        canonical(read_numeric_record(directory / "launch.json"))
    )
    identity = NumericIdentity.model_validate_json(
        canonical(read_numeric_record(directory / "identity.json"))
    )
    if launch.intent.admission != admission:
        raise ValueError("Candidate admission differs")
    receipt_matches(identity, receipts["launch_identity"])
    client = NumericDockerClient()
    client.deadline = original_deadline(launch.intent.not_after)
    observed = observe_leader(
        client, identity.container_id, identity.image_id, identity.init_pid
    )
    if observed != (
        identity.init_start_ticks,
        identity.pid_namespace_inode,
        identity.boot_id,
    ):
        raise ValueError("Actual candidate kernel identity differs")
    value = client._inspect(identity.container_id)
    if (
        value is None
        or value["Id"] != identity.container_id
        or value["Image"] != identity.image_id
    ):
        raise ValueError("Candidate container binding differs")
    require_boundary(value, host_pid=False, candidate=True)
    if identity.pid_namespace_inode == os.stat("/proc/self/ns/pid").st_ino:
        raise ValueError("Candidate shares trusted host PID namespace")
    candidate_child(client, identity)
    return True


def probe(owner):
    from sqlalchemy import select

    from backend.app.core.config import get_settings
    from backend.app.core.db import create_db_engine, session_factory
    from backend.app.core.maintenance import shared_lifecycle
    from backend.app.research_process_models import (
        ResearchProcessAdmission,
        ResearchProcessReceipt,
    )
    from packages.research_runtime.process_persistence_codec import (
        ROW_FIELDS,
        decode_row,
    )

    settings = get_settings()
    engine = create_db_engine(settings)
    factory = session_factory(engine)
    seen = set()
    try:
        with shared_lifecycle(engine, settings.storage_root):
            deadline = time.monotonic() + 360
            while time.monotonic() < deadline:
                with factory() as session:
                    rows = session.scalars(
                        select(ResearchProcessAdmission)
                        .where(
                            ResearchProcessAdmission.owner_id == owner,
                            ResearchProcessAdmission.role.in_(
                                ["rd_orchestrator", "factor_cutoff_candidate"]
                            ),
                        )
                        .limit(3)
                    ).all()
                    if len(rows) > 2 or len({r.run_id for r in rows}) > 1:
                        raise ValueError("Disposable owner run inventory differs")
                    for row in rows:
                        if row.role in seen:
                            continue
                        receipts = {
                            r.kind: r
                            for r in session.scalars(
                                select(ResearchProcessReceipt)
                                .where(
                                    ResearchProcessReceipt.owner_id == owner,
                                    ResearchProcessReceipt.admission_id == row.id,
                                )
                                .limit(6)
                            ).all()
                        }
                        required = {"launch_identity"} | (
                            {"controller_child"}
                            if row.role == "rd_orchestrator"
                            else set()
                        )
                        if not required <= receipts.keys():
                            continue
                        admission = decode_row({k: getattr(row, k) for k in ROW_FIELDS})
                        # Once durable identity exists, a mismatch is fatal, not a retry.
                        check = (
                            controller if row.role == "rd_orchestrator" else candidate
                        )
                        check(settings.storage_root, admission, receipts)
                        seen.add(row.role)
                if len(seen) == 2:
                    RESULT.write_text(
                        json.dumps(
                            {
                                "actual_peer_and_host_identity": True,
                                "unrelated_pid_rejected": True,
                                "candidate_private_namespace": True,
                                "candidate_privileges_dropped": True,
                            }
                        )
                    )
                    return
                time.sleep(0.05)
        raise ValueError("Live identity proof not observed")
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        if sys.argv[1] == "applications":
            applications(sys.argv[2:])
        elif sys.argv[1] == "probe":
            probe(UUID(os.environ["MINEAGENT_COMPOSE_OWNER"]))
        else:
            raise ValueError("Unknown identity mode")
    except Exception:  # noqa: BLE001 - no private host diagnostics in artifacts
        raise SystemExit("Compose identity proof failed") from None
