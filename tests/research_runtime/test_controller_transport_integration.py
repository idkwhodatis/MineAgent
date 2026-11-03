"""Local synthetic daemon/proc evidence through the original peer authenticators.

No Docker, network, database, protected UID process or performance proof. The
shared path exercises its real HTTP parser; only its OS I/O is doubled. The CLI
comparison retains DockerSupervisor._inspect and the current observer/peer
methods, substituting deterministic command output rather than launching Docker.
"""

import ast
import copy
import hashlib
import io
import json
import socket
import stat
import struct
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from packages.rdagent_adapter.controller_profile import (
    COMPOSED_CONTROLLER_PROFILE,
    LEGACY_CONTROLLER_PROFILE,
    SEEDED_CONTROLLER_PROFILE,
)
from packages.research_runtime.process_contracts import ProcessError
from packages.research_runtime.process_declarations_v2 import (
    HostTicketIPC,
    ProcessDeclarationV2,
    declaration_bytes,
)
from runtimes.openevolve_operational import daemon_identity as oe_observer
from runtimes.openevolve_operational import peer as oe_peer
from runtimes.rdagent_operational import daemon_identity as rd_observer
from runtimes.rdagent_operational import peer as rd_peer
from runtimes.research_supervisor import docker_observation_transport as transport
from runtimes.research_supervisor.supervisor import DockerSupervisor

CID = "c" * 64
IMAGE = "sha256:" + "d" * 64
PROCESS_TICKET = "a" * 64
WORKER_TICKET = "b" * 64
TOP_FIELDS = "pid,uid,ppid,pidns,comm"
PROFILES = {
    "rd60": (LEGACY_CONTROLLER_PROFILE, 60),
    "rd120": (COMPOSED_CONTROLLER_PROFILE, 120),
    "rd180": (SEEDED_CONTROLLER_PROFILE, 180),
    "oe_controller": (oe_peer.PROFILE, 600),
    "oe_worker": (oe_peer.PROFILE, 600),
}


class CredentialSocket(socket.socket):
    """No descriptor is opened; real peer._credentials checks this typed double."""

    def __init__(self, world):
        self.world = world

    @property
    def family(self):
        return socket.AF_UNIX

    def getsockopt(self, level, option, *args):
        assert level == socket.SOL_SOCKET
        if option == socket.SO_TYPE:
            assert not args
            return socket.SOCK_STREAM
        assert (option, args) == (socket.SO_PEERCRED, (struct.calcsize("3i"),))
        credentials = self.world.event("credentials", lambda: self.world.credentials)
        return struct.pack("3i", *credentials)


class DaemonConnection:
    def __init__(self, world):
        self.world = world
        self.closed = False
        self.requests = []
        self.timeouts = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def settimeout(self, value):
        assert 0 < value <= min(10, self.world.context.deadline - self.world.now)
        self.timeouts.append(value)

    def connect(self, path):
        assert path == "/var/run/docker.sock"

    def getsockopt(self, level, option, size):
        assert (level, option, size) == (socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        return struct.pack("3i", 42, 0, 0)

    def sendall(self, request):
        assert not self.requests
        self.requests.append(request)
        inspect = request.startswith(
            f"GET /v1.45/containers/{CID}/json HTTP/1.1\r\n".encode()
        )
        path = (
            f"/v1.45/containers/{CID}/json"
            if inspect
            else f"/v1.45/containers/{CID}/top?ps_args=-eo%20pid%2Cuid%2Cppid%2Cpidns%2Ccomm"
        )
        assert request == (
            f"GET {path} HTTP/1.1\r\nHost: docker\r\nConnection: close\r\n"
            "Accept: application/json\r\n\r\n"
        ).encode("ascii")
        value = self.world.daemon(inspect)
        raw = json.dumps(value, separators=(",", ":")).encode()
        self.raw = io.BytesIO(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
            + str(len(raw)).encode()
            + b"\r\n\r\n"
            + raw
        )

    def recv_into(self, target):
        return self.raw.readinto(target)


class World:
    def __init__(self, monkeypatch, role, mode="shared", seconds=None):
        self.role = role
        self.observer = rd_observer if role.startswith("rd") else oe_observer
        self.peers = rd_peer if role.startswith("rd") else oe_peer
        self.now = 1000.0
        self.events = []
        self.counts = Counter()
        self.connections = []
        self.before = lambda event: None
        self.after = lambda event: None
        self.document = {
            "Id": CID,
            "Image": IMAGE,
            "State": {"Pid": 100, "Running": True, "Status": "running"},
        }
        self.hosts = {
            100: (100, 1000, "0::/unit\n", 1, (0,) * 4, (0,) * 4),
            101: (101, 1001, "0::/unit\n", 100, (65533,) * 4, (65533,) * 4),
            102: (102, 1002, "0::/unit\n", 101, (65533,) * 4, (65533,) * 4),
        }
        self.namespaces = dict.fromkeys(self.hosts, 4026532777)
        self.credentials = (102 if role == "oe_worker" else 101, 65533, 65533)
        profile, limit = PROFILES[role]
        self.admission = ProcessDeclarationV2(
            schema_version="mineagent.process-admission.v2",
            id=uuid4(),
            owner_id=uuid4(),
            run_id=uuid4(),
            attempt_id=uuid4(),
            generation=1,
            role="rd_orchestrator"
            if role.startswith("rd")
            else "openevolve_orchestrator",
            logical_operation_id=uuid4(),
            launch_ordinal=1,
            profile=profile,
            input_sha256="e" * 64,
            reservation_id=uuid4(),
            image_id=IMAGE,
            ipc=HostTicketIPC(
                ipc_policy="host_ticket",
                ticket_sha256=hashlib.sha256(PROCESS_TICKET.encode()).hexdigest(),
            ),
        )
        identities = {
            pid: rd_peer.KernelIdentity(
                host[0], host[1], f"pid:[{self.namespaces[pid]}]", *host[2:]
            )
            for pid, host in self.hosts.items()
        }
        digest = hashlib.sha256(declaration_bytes(self.admission)).hexdigest()
        if role.startswith("rd"):
            self.binding = rd_peer.TrustedControllerBinding(
                digest, identities[100], identities[101]
            )
        else:
            self.binding = oe_peer.ControllerBinding(
                CID, digest, identities[100], identities[101]
            )
            if role == "oe_worker":
                self.binding = oe_peer.WorkerBinding(
                    self.binding,
                    uuid4(),
                    uuid4(),
                    hashlib.sha256(WORKER_TICKET.encode()).hexdigest(),
                    identities[102],
                )
        self.context = self.observer.DaemonIdentityContext(
            CID,
            IMAGE,
            100,
            self.now + (limit if seconds is None else seconds),
            **({"profile": profile} if role.startswith("rd") else {}),
        )
        self.fence = SimpleNamespace(
            **{
                key: getattr(self.admission, key)
                for key in ("owner_id", "run_id", "attempt_id", "generation")
            }
        )
        self.channel = CredentialSocket(self)
        assert self.channel.fileno() == -1
        clock = SimpleNamespace(monotonic=lambda: self.now)
        monkeypatch.setattr(self.observer, "time", clock)
        monkeypatch.setattr(transport, "time", clock)
        original_snapshot = self.observer._host_snapshot
        monkeypatch.setattr(self.observer, "_read", self.read)
        monkeypatch.setattr(
            self.observer,
            "_host_snapshot",
            lambda pid: self.event("host", lambda: original_snapshot(pid), pid),
        )
        monkeypatch.setattr(
            transport,
            "os",
            SimpleNamespace(
                lstat=lambda path: SimpleNamespace(
                    st_mode=stat.S_IFSOCK | 0o660,
                    st_uid=0,
                    st_gid=999,
                    st_dev=1,
                    st_ino=2,
                )
            ),
        )
        monkeypatch.setattr(
            transport,
            "socket",
            SimpleNamespace(
                socket=self.socket,
                AF_UNIX=socket.AF_UNIX,
                SOCK_STREAM=socket.SOCK_STREAM,
                SOL_SOCKET=socket.SOL_SOCKET,
                SO_PEERCRED=socket.SO_PEERCRED,
            ),
        )
        if mode == "cli":
            world = self

            class CommandOutput(DockerSupervisor):
                def __init__(self, image_id, **kwargs):
                    super().__init__(image_id)
                    assert kwargs == {
                        "deadline": world.context.deadline,
                        "identity_code": world.identity_code,
                    }

                def _command(self, args, *, check=True):
                    if args == ["container", "inspect", CID]:
                        assert check is False
                        return 0, json.dumps([world.daemon(True)], indent=4).encode()
                    assert args == ["top", CID, "-eo", TOP_FIELDS]
                    value = world.daemon(False)
                    return 0, (
                        "\n".join(
                            " ".join(row)
                            for row in [value["Titles"], *value["Processes"]]
                        )
                        + "\n"
                    ).encode()

            monkeypatch.setattr(self.observer, "DockerSupervisor", CommandOutput)
        else:
            # A production wiring regression must fail, not be patched away here.
            assert (
                self.observer.DockerSupervisor is transport.DockerObservationTransport
            )

    @property
    def identity_code(self):
        return (
            "rd_controller_daemon_identity"
            if self.role.startswith("rd")
            else "oe_controller_daemon_identity"
        )

    @property
    def peer_code(self):
        return (
            "rd_controller_peer"
            if self.role.startswith("rd")
            else "openevolve_protected_peer"
        )

    def event(self, kind, getter, pid=None):
        event = (kind, self.counts[kind], pid)
        self.counts[kind] += 1
        self.events.append(event)
        self.before(event)
        value = copy.deepcopy(getter())
        self.after(event)
        return value

    def socket(self, family, kind):
        assert (family, kind) == (socket.AF_UNIX, socket.SOCK_STREAM)
        connection = DaemonConnection(self)
        self.connections.append(connection)
        return connection

    def daemon(self, inspect):
        if inspect:
            return self.event("inspect", lambda: self.document)
        return self.event(
            "top",
            lambda: {
                "Titles": ["PID", "UID", "PPID", "PIDNS", "COMMAND"],
                "Processes": [
                    [
                        str(pid),
                        str(host[4][1]),
                        str(host[3]),
                        str(self.namespaces[pid]),
                        "python",
                    ]
                    for pid, host in self.hosts.items()
                ],
            },
        )

    def read(self, pid, name, maximum):
        host = self.hosts[pid]
        if name == "stat":
            fields = ["S", str(host[3]), *("0" for _ in range(17)), str(host[1])]
            value = f"{host[0]} (python) " + " ".join(fields)
        elif name == "status":
            value = (
                f"Pid:\t{host[0]}\nPPid:\t{host[3]}\nState:\tS (sleeping)\n"
                + "Uid:\t"
                + " ".join(map(str, host[4]))
                + "\n"
                + "Gid:\t"
                + " ".join(map(str, host[5]))
                + "\n"
            )
        else:
            assert name == "cgroup"
            value = host[2]
        assert 0 < len(value.encode()) <= maximum
        return value

    def authenticate(self, **changes):
        if self.role.startswith("rd"):
            kwargs = {
                "binding": self.binding,
                "supplied_ticket": PROCESS_TICKET,
                "observer_context": self.context,
            }
            kwargs.update(changes)
            return self.peers.authenticate_controller(
                self.channel, self.admission, **kwargs
            )
        kwargs = {
            "binding": self.binding,
            "context": self.context,
            "process_ticket": PROCESS_TICKET,
        }
        if self.role == "oe_worker":
            kwargs["worker_ticket"] = WORKER_TICKET
        kwargs.update(changes)
        return self.peers.authenticate(self.channel, self.admission, **kwargs)

    def require(self, peer):
        if self.role.startswith("rd"):
            return self.peers.require_controller_peer(
                peer,
                self.fence,
                logical_operation_id=self.admission.logical_operation_id,
            )
        kwargs = {"admission_id": self.admission.id}
        if self.role == "oe_worker":
            kwargs.update(
                epoch_id=self.binding.epoch_id, intent_id=self.binding.intent_id
            )
        return self.peers.require_peer(peer, self.fence, **kwargs)

    def mutate(self, pid, field):
        if field == "namespace":
            self.namespaces[pid] += 1
            return
        host = list(self.hosts[pid])
        index = {"pid": 0, "start": 1, "cgroup": 2, "ppid": 3, "uid": 4, "gid": 5}[
            field
        ]
        host[index] = (
            "0::/other\n"
            if field == "cgroup"
            else (12345,) * 4
            if field in ("uid", "gid")
            else host[index] + 1
        )
        self.hosts[pid] = tuple(host)


def expected_events(role):
    counts = Counter()
    result = []
    for target in [101, 102, 101] if role == "oe_worker" else [101]:
        for kind, pid in (
            ("inspect", None),
            ("host", 100),
            ("host", target),
            ("top", None),
            ("host", 100),
            ("host", target),
            ("inspect", None),
        ):
            result.append((kind, counts[kind], pid))
            counts[kind] += 1
    result.append(("credentials", 0, None))
    return result


@pytest.mark.parametrize("role", PROFILES)
@pytest.mark.parametrize("mode", ["cli", "shared"])
def test_full_peer_authentication_and_every_event_reobserve(monkeypatch, role, mode):
    world = World(monkeypatch, role, mode)
    peer = world.authenticate()
    assert world.events == expected_events(role)
    assert world.require(peer) is peer
    assert world.require(peer) is peer
    factor = 3 if role == "oe_worker" else 1
    assert world.counts == Counter(
        inspect=6 * factor, top=3 * factor, host=12 * factor, credentials=3
    )
    if mode == "shared":
        assert len(world.connections) == 9 * factor
        assert len({id(connection) for connection in world.connections}) == 9 * factor
        assert all(
            connection.closed and len(connection.requests) == 1
            for connection in world.connections
        )
    with pytest.raises(TypeError):
        peer.__reduce__()


@pytest.mark.parametrize("role", PROFILES)
@pytest.mark.parametrize("seconds", [0, -1, "too_long"])
def test_original_profile_window_rejects_before_daemon(monkeypatch, role, seconds):
    limit = PROFILES[role][1]
    world = World(
        monkeypatch, role, seconds=limit + 1 if seconds == "too_long" else seconds
    )
    with pytest.raises(ProcessError) as error:
        world.authenticate()
    assert error.value.code == world.identity_code
    assert not world.events and not world.connections


@pytest.mark.parametrize("role", PROFILES)
def test_provider_eight_second_context_never_renews(monkeypatch, role):
    world = World(monkeypatch, role, seconds=8)
    deadline = world.context.deadline
    peer = world.authenticate()
    world.now += 7.5
    assert world.require(peer) is peer
    assert all(
        value <= 0.5
        for connection in world.connections[3 if role != "oe_worker" else 9 :]
        for value in connection.timeouts
    )
    before = list(world.events)
    count = len(world.connections)
    world.now = deadline
    with pytest.raises(ProcessError) as error:
        world.require(peer)
    assert error.value.code == world.identity_code
    assert world.events == before and len(world.connections) == count
    assert world.context.deadline == deadline


DAEMON_SEAMS = [
    (role, event)
    for role in ("rd60", "oe_worker")
    for event in expected_events(role)
    if event[0] in ("inspect", "top")
]


@pytest.mark.parametrize("role,seam", DAEMON_SEAMS)
@pytest.mark.parametrize(
    "code",
    [
        "research_process_uncertain",
        "research_process_output",
        "research_process_identity",
        "research_process_image",
    ],
)
def test_process_error_from_every_daemon_seam_propagates_unchanged(
    monkeypatch, role, seam, code
):
    world = World(monkeypatch, role)
    failure = ProcessError(code)

    def fail(event):
        if event == seam:
            raise failure

    world.before = fail
    with pytest.raises(ProcessError) as error:
        world.authenticate()
    assert error.value is failure
    assert world.events[-1] == seam
    assert world.counts["credentials"] == 0
    assert all(connection.closed for connection in world.connections)


@pytest.mark.parametrize("role,seam", DAEMON_SEAMS)
def test_expiry_at_each_daemon_seam_has_no_retry_or_new_window(monkeypatch, role, seam):
    world = World(monkeypatch, role, seconds=8)
    deadline = world.context.deadline
    world.after = lambda event: (
        setattr(world, "now", deadline) if event == seam else None
    )
    with pytest.raises(ProcessError) as error:
        world.authenticate()
    assert error.value.code == world.identity_code
    assert world.events[-1] == seam
    assert world.counts["credentials"] == 0
    assert world.context.deadline == deadline
    assert all(connection.closed for connection in world.connections)


@pytest.mark.parametrize(
    "role,pid",
    [
        ("rd60", 100),
        ("rd60", 101),
        ("oe_worker", 100),
        ("oe_worker", 101),
        ("oe_worker", 102),
    ],
)
@pytest.mark.parametrize(
    "field", ["pid", "start", "namespace", "uid", "gid", "cgroup", "ppid"]
)
def test_next_event_rejects_identity_drift_without_cached_authority(
    monkeypatch, role, pid, field
):
    results = []
    for mode in ("cli", "shared"):
        with monkeypatch.context() as patch:
            world = World(patch, role, mode)
            peer = world.authenticate()
            world.mutate(pid, field)
            with pytest.raises(ProcessError) as error:
                world.require(peer)
            results.append((error.value.code, world.events))
    assert results[0] == results[1]


@pytest.mark.parametrize("role", ["rd60", "oe_worker"])
@pytest.mark.parametrize("index", [0, 1, 2])
def test_actual_peer_credential_decoder_rejects_pid_uid_gid_drift(
    monkeypatch, role, index
):
    world = World(monkeypatch, role)
    peer = world.authenticate()
    values = list(world.credentials)
    values[index] += 1
    world.credentials = tuple(values)
    with pytest.raises(ProcessError) as error:
        world.require(peer)
    assert error.value.code == world.peer_code
    assert world.counts["credentials"] == 2


@pytest.mark.parametrize("role", ["rd60", "oe_worker"])
def test_original_ticket_claim_is_checked_before_transport(monkeypatch, role):
    world = World(monkeypatch, role)
    key = "supplied_ticket" if role.startswith("rd") else "process_ticket"
    with pytest.raises(ProcessError) as error:
        world.authenticate(**{key: "f" * 64})
    assert error.value.code == world.peer_code
    assert not world.events and not world.connections


def test_worker_ticket_and_exact_assignment_still_required(monkeypatch):
    world = World(monkeypatch, "oe_worker")
    with pytest.raises(ProcessError, match="openevolve_protected_peer"):
        world.authenticate(worker_ticket="f" * 64)
    assert not world.events
    peer = world.authenticate()
    world.binding = replace(world.binding, intent_id=uuid4())
    with pytest.raises(ProcessError, match="openevolve_protected_peer"):
        world.require(peer)


def test_namespace_drift_after_worker_first_pair_keeps_final_pair_check(monkeypatch):
    world = World(monkeypatch, "oe_worker")
    original_hosts = copy.deepcopy(world.hosts)
    world.after = lambda event: (
        world.mutate(101, "namespace") if event == ("top", 0, None) else None
    )
    with pytest.raises(ProcessError, match="openevolve_protected_peer"):
        world.authenticate()
    assert world.hosts == original_hosts
    assert world.counts == Counter(inspect=6, top=3, host=12)
    assert world.events == expected_events("oe_worker")[:-1]


@pytest.mark.parametrize("role", ["rd60", "oe_worker"])
@pytest.mark.parametrize("field", ["pid", "start", "uid", "gid", "cgroup", "ppid"])
def test_mid_observation_host_drift_rejects_before_peer_issuance(
    monkeypatch, role, field
):
    world = World(monkeypatch, role)
    world.after = lambda event: (
        world.mutate(101, field) if event == ("top", 0, None) else None
    )
    with pytest.raises(ProcessError) as error:
        world.authenticate()
    assert error.value.code == world.identity_code
    assert world.counts["credentials"] == 0
    assert world.counts["top"] == 1


@pytest.mark.parametrize(
    "role,seam", [(role, seam) for role, seam in DAEMON_SEAMS if seam[0] == "inspect"]
)
@pytest.mark.parametrize("field", ["Id", "Image", "Pid", "Running", "Status"])
def test_each_inspect_rejects_changed_container_identity(
    monkeypatch, role, seam, field
):
    world = World(monkeypatch, role)

    def change(event):
        if event == seam:
            target = (
                world.document if field in ("Id", "Image") else world.document["State"]
            )
            target[field] = {
                "Id": "f" * 64,
                "Image": "sha256:" + "f" * 64,
                "Pid": 999,
                "Running": False,
                "Status": "paused",
            }[field]

    world.before = change
    with pytest.raises(ProcessError) as error:
        world.authenticate()
    assert error.value.code == world.identity_code
    assert world.events[-1] == seam
    assert world.counts["credentials"] == 0


def test_shared_transport_direct_imports_have_no_engine_observer_dependency():
    tree = ast.parse(Path(transport.__file__).read_text())
    imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    for node in imports:
        if isinstance(node, ast.Import):
            assert all(
                alias.name.split(".")[0] in sys.stdlib_module_names
                for alias in node.names
            )
        elif node.module == "packages.research_runtime.process_contracts":
            assert node.level == 0
            assert [alias.name for alias in node.names] == ["ProcessError"]
        else:
            assert (
                node.level == 0 and node.module.split(".")[0] in sys.stdlib_module_names
            )


def test_shared_transport_is_in_host_image_but_not_protected_runtime_inventory():
    root = Path(__file__).resolve().parents[2]
    dockerfile = (root / "infra/application.Dockerfile").read_text().splitlines()
    ignore = (
        (root / "infra/application.Dockerfile.dockerignore").read_text().splitlines()
    )
    assert "COPY runtimes ./runtimes" in dockerfile
    assert "!**/" in ignore and "!runtimes/**/*.py" in ignore
    module_path = "runtimes/research_supervisor/docker_observation_transport.py"
    assert (root / module_path).is_file()

    build_tree = ast.parse(
        (root / "runtimes/rdagent_operational/build_profile.py").read_text()
    )
    files = next(
        ast.literal_eval(node.value)
        for node in build_tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "FILES"
            for target in node.targets
        )
    )
    forbidden = {
        module_path,
        "runtimes/rdagent_operational/daemon_identity.py",
        "runtimes/openevolve_operational/daemon_identity.py",
    }
    assert not forbidden.intersection(files)
    assert all(not name.startswith("runtimes/research_supervisor/") for name in files)

    oe = (root / "runtimes/openevolve_operational/Dockerfile").read_text()
    sources = [
        source
        for line in oe.splitlines()
        if line.startswith("COPY ")
        for source in line.split()[1:-1]
        if not source.startswith("--")
    ]
    assert not forbidden.intersection(sources)
    assert all(
        not source.startswith("runtimes/research_supervisor") for source in sources
    )
    assert not {
        "runtimes",
        "runtimes/",
        "runtimes/rdagent_operational",
        "runtimes/openevolve_operational",
    }.intersection(sources)
    assert not any("*" in source for source in sources)
