"""Real bounded journal reads plus persistence doubles; no PG/Docker claim."""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from packages.research_runtime.process_contracts import digest
from packages.research_runtime.process_declarations_v2 import (
    HostTicketIPC,
    ProcessDeclarationV2,
)
from runtimes.openevolve_operational import process_journal as module
from runtimes.openevolve_operational.trusted_report import report_bytes
from runtimes.rdagent_operational.peer import KernelIdentity


def records(root):
    admission = ProcessDeclarationV2(
        schema_version="mineagent.process-admission.v2",
        id=uuid4(),
        owner_id=uuid4(),
        run_id=uuid4(),
        attempt_id=uuid4(),
        generation=1,
        role="openevolve_orchestrator",
        logical_operation_id=uuid4(),
        launch_ordinal=1,
        profile=module.PROFILE,
        input_sha256=digest(
            {
                "schema_version": "mineagent.openevolve-bootstrap-inventory.v1",
                "files": [
                    {
                        "destination": "/bootstrap/bootstrap.json",
                        "sha256": "a" * 64,
                        "size_bytes": 512,
                    },
                    {
                        "destination": "/bootstrap/seed.py",
                        "sha256": "f" * 64,
                        "size_bytes": 10,
                    },
                ],
            }
        ),
        reservation_id=uuid4(),
        image_id="sha256:" + "b" * 64,
        ipc=HostTicketIPC(ipc_policy="host_ticket", ticket_sha256="c" * 64),
    )
    now = datetime.now(UTC)
    intent = module.ControllerIntent(
        schema_version="mineagent.oe-controller-intent.v1",
        id=uuid4(),
        admission=admission,
        bootstrap_sha256="a" * 64,
        bootstrap_size_bytes=512,
        seed_sha256="f" * 64,
        seed_size_bytes=10,
        input_layout="bootstrap_json_and_seed_py_only",
        issued_at=now,
        not_after=now + timedelta(seconds=600),
    )
    info = root.stat()
    launch = module.ControllerLaunch(
        schema_version="mineagent.oe-controller-launch.v1",
        intent=intent,
        runtime_name="mineagent-oe-controller-" + str(admission.id),
        root_device=info.st_dev,
        root_inode=info.st_ino,
    )
    leader = KernelIdentity(100, 1000, "pid:[42]", "0::/unit\n", 1, (0,) * 4, (0,) * 4)
    identity = module.ControllerIdentity(
        schema_version="mineagent.oe-controller-identity.v1",
        admission_sha256=digest(admission.model_dump(mode="json")),
        launch_sha256=digest(launch.model_dump(mode="json")),
        container_id="d" * 64,
        image_id=admission.image_id,
        root_device=info.st_dev,
        root_inode=info.st_ino,
        boot_id=uuid4(),
        leader=leader,
    )
    kernel = KernelIdentity(
        101, 1001, "pid:[42]", "0::/unit\n", 100, (65533,) * 4, (65533,) * 4
    )
    child = module.ControllerChild(
        schema_version="mineagent.oe-controller-child.v1",
        identity_sha256=digest(identity.model_dump(mode="json")),
        admission_sha256=identity.admission_sha256,
        controller=kernel,
        namespace_pids=(101, 2),
        namespace_pid=2,
        started_report_sha256=hashlib.sha256(
            report_bytes("started", 2, 1001)
        ).hexdigest(),
        peer_pid=101,
        peer_uid=65533,
        peer_gid=65533,
    )
    stop = module.ControllerStop(
        schema_version="mineagent.oe-controller-stop.v1",
        launch_sha256=identity.launch_sha256,
        identity_sha256=child.identity_sha256,
        child_sha256=digest(child.model_dump(mode="json")),
        container_id=identity.container_id,
        confirmed_absent=True,
        kernel_identity_absent=True,
        child_kernel_identity_absent=True,
    )
    return launch, identity, child, stop
