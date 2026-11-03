"""Synthetic adapter/order audit; no sockets, Docker, or physical elapsed claims.

The wire-level transport suite owns HTTP/socket bounds. Here _get is always
replaced and the existing observer/peer code is exercised without product edits.
"""

import copy
import hashlib
import json
from collections import Counter
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from packages.research_runtime.process_contracts import ProcessError
from packages.research_runtime.process_declarations_v2 import (
    HostTicketIPC,
    ProcessDeclarationV2,
    declaration_bytes,
)
from runtimes.openevolve_operational import daemon_identity as observer
from runtimes.openevolve_operational import docker_read_transport as transport
from runtimes.openevolve_structured_host import peer as peer_module
from runtimes.rdagent_operational.peer import KernelIdentity

CID = "e" * 64
IMAGE = "sha256:" + "d" * 64
TITLES = ["PID", "UID", "PPID", "PIDNS", "COMMAND"]
TOP_ARGS = ["top", CID, "-eo", observer.TOP_FIELDS]
TOP_VALUE = {
    "Titles": TITLES,
    "Processes": [
        ["100", "0", "1", "42", "python"],
        ["101", "65533", "100", "42", "python"],
        ["102", "65533", "101", "42", "python"],
    ],
}


@pytest.fixture(autouse=True)
def no_real_socket(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("This synthetic suite must not open a socket")

    monkeypatch.setattr(transport.socket, "socket", forbidden)


@pytest.fixture
def adapter(monkeypatch):
    calls = []
    payload = copy.deepcopy(TOP_VALUE)

    def get(path, deadline):
        calls.append((path, deadline))
        return copy.deepcopy(payload)

    monkeypatch.setattr(transport, "_get", get)
    monkeypatch.setattr(transport, "time", SimpleNamespace(monotonic=lambda: 10.0))
    return SimpleNamespace(
        value=transport.DockerReadTransport(IMAGE, deadline=60.0),
        calls=calls,
        payload=payload,
    )


def test_exact_read_only_top_route_and_cli_padded_projection(adapter):
    code, raw = adapter.value._command(TOP_ARGS)
    assert code == 0
    assert adapter.calls == [
        (
            f"{transport.API}/containers/{CID}/top?ps_args=-eo%20pid%2Cuid%2Cppid%2Cpidns%2Ccomm",
            60.0,
        )
    ]
    expected = "".join(
        "".join(cell.ljust(20) for cell in row[:4]) + row[4] + "\n"
        for row in [TITLES, *TOP_VALUE["Processes"]]
    ).encode("ascii")
    assert raw == expected
    assert observer.parse_top(raw) == {
        100: (0, 1, 42),
        101: (65533, 100, 42),
        102: (65533, 101, 42),
    }


def test_top_long_numeric_fields_preserve_tabwriter_padding(adapter):
    adapter.payload["Processes"][0][0] = "1" * 20
    _, raw = adapter.value._command(TOP_ARGS)
    assert raw.splitlines()[0].startswith(b"PID" + b" " * 20 + b"UID")
    assert raw.splitlines()[1].startswith(b"1" * 20 + b" " * 3 + b"0")


def test_top_bound_applies_to_padded_not_compact_projection(adapter, monkeypatch):
    compact = (
        "\n".join(" ".join(row) for row in [TITLES, *TOP_VALUE["Processes"]]) + "\n"
    ).encode()
    _, padded = adapter.value._command(TOP_ARGS)
    assert len(compact) < len(padded)
    monkeypatch.setattr(observer, "MAX_TOP_BYTES", len(padded) - 1)
    with pytest.raises(ProcessError):
        adapter.value._command(TOP_ARGS)
    monkeypatch.setattr(observer, "MAX_TOP_BYTES", len(padded))
    assert adapter.value._command(TOP_ARGS)[1] == padded


def test_exact_inspect_object_route_without_envelope_or_cache(adapter):
    adapter.payload.clear()
    adapter.payload.update({"Id": CID, "Image": IMAGE})
    assert adapter.value._inspect(CID) == adapter.payload
    adapter.payload["Image"] = "sha256:" + "f" * 64
    assert adapter.value._inspect(CID)["Image"] == "sha256:" + "f" * 64
    assert adapter.calls == [(f"{transport.API}/containers/{CID}/json", 60.0)] * 2


@pytest.mark.parametrize(
    "args",
    [
        None,
        (),
        TOP_ARGS + ["extra"],
        ["inspect", CID, "-eo", observer.TOP_FIELDS],
        ["top", CID, "-ef", observer.TOP_FIELDS],
        ["top", CID, "-eo", "pid,comm"],
        ["top", "container-name", "-eo", observer.TOP_FIELDS],
    ],
)
def test_no_arbitrary_operation_or_field_expansion(adapter, args):
    with pytest.raises(ProcessError):
        adapter.value._command(args)
    assert adapter.calls == []


@pytest.mark.parametrize("cid", ["", "a" * 63, "A" * 64, "a" * 63 + "/", True, None])
def test_exact_container_id_before_any_get(adapter, cid):
    with pytest.raises(ProcessError):
        adapter.value._inspect(cid)
    assert adapter.calls == []


@pytest.mark.parametrize("image", ["latest", "sha256:" + "a" * 63, None, True])
def test_exact_image_type(image):
    with pytest.raises(ProcessError):
        transport.DockerReadTransport(image, deadline=60.0)


@pytest.mark.parametrize("deadline", [True, None, float("nan"), float("inf")])
def test_finite_deadline_type(deadline):
    with pytest.raises(ProcessError):
        transport.DockerReadTransport(IMAGE, deadline=deadline)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_titles",
        "extra_key",
        "wrong_titles",
        "tuple_rows",
        "empty_rows",
        "too_many",
        "short_row",
        "numeric_cell",
        "bool_cell",
        "null_cell",
        "empty_cell",
        "space",
        "newline",
        "non_ascii",
        "duplicate_pid",
        "zero_namespace",
        "wrong_uid",
        "negative_pid",
        "long_comm",
    ],
)
def test_top_malformed_or_unbounded_projection_closed(adapter, mutation):
    payload = adapter.payload
    if mutation == "missing_titles":
        del payload["Titles"]
    elif mutation == "extra_key":
        payload["Extra"] = "never authority"
    elif mutation == "wrong_titles":
        payload["Titles"][-1] = "CMD"
    elif mutation == "tuple_rows":
        payload["Processes"] = tuple(payload["Processes"])
    elif mutation == "empty_rows":
        payload["Processes"] = []
    elif mutation == "too_many":
        payload["Processes"] = [[str(i), "0", "1", "42", "p"] for i in range(1, 66)]
    elif mutation == "short_row":
        payload["Processes"][0].pop()
    elif mutation == "duplicate_pid":
        payload["Processes"].append(payload["Processes"][0])
    else:
        column, value = {
            "numeric_cell": (0, 100),
            "bool_cell": (0, True),
            "null_cell": (0, None),
            "empty_cell": (4, ""),
            "space": (4, "two words"),
            "newline": (4, "p\n"),
            "non_ascii": (4, "é"),
            "zero_namespace": (3, "0"),
            "wrong_uid": (1, "root"),
            "negative_pid": (0, "-1"),
            "long_comm": (4, "p" * 65),
        }[mutation]
        payload["Processes"][0][column] = value
    with pytest.raises(ProcessError):
        adapter.value._command(TOP_ARGS)
    assert len(adapter.calls) == 1


def test_top_maximum_valid_inventory_and_unobserved_rows_are_not_dropped(adapter):
    adapter.payload["Processes"] = [[str(i), "0", "1", "42", "p"] for i in range(1, 65)]
    assert len(observer.parse_top(adapter.value._command(TOP_ARGS)[1])) == 64
    adapter.payload["Processes"][-1][3] = "0"
    with pytest.raises(ProcessError):
        adapter.value._command(TOP_ARGS)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":1}',
        b'{"a":1e+100,"b":"{}[],:\\""}',
        b'{ "nested" : [ {}, [], {"b":"\\\\"} ] }',
        b'{"nested":{"empty":[]},"unicode":"\\u0061"}',
    ],
)
def test_inspect_conservative_bound_accepts_small_lexical_tokens(raw):
    transport._inspect_render_bound(raw)


def test_inspect_compact_body_cannot_bypass_cli_pretty_output_limit():
    payload = {"items": [0] * 10000}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    assert len(raw) < transport.MAX_BODY
    assert len((json.dumps([payload], indent=4) + "\n").encode()) > transport.MAX_BODY
    with pytest.raises(ProcessError):
        transport._inspect_render_bound(raw)


def test_inspect_conservative_bound_exact_inclusive_edge(monkeypatch):
    raw = b'{"value":"x"}'
    # Outer array/newline allowance16, object open13 + close13, colon1.
    bound = len(raw) + 43
    monkeypatch.setattr(transport, "MAX_BODY", bound)
    transport._inspect_render_bound(raw)
    monkeypatch.setattr(transport, "MAX_BODY", bound - 1)
    with pytest.raises(ProcessError):
        transport._inspect_render_bound(raw)


def test_inspect_bound_ignores_structural_characters_inside_escaped_strings(
    monkeypatch,
):
    payload = {"value": '{}[],:"\\' * 100}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    monkeypatch.setattr(transport, "MAX_BODY", len(raw) + 43)
    transport._inspect_render_bound(raw)


def test_inspect_bound_is_documented_stricter_not_byte_equivalent():
    payload = {"items": [[]] * 2000}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    assert len((json.dumps([payload], indent=4) + "\n").encode()) <= transport.MAX_BODY
    with pytest.raises(ProcessError):
        transport._inspect_render_bound(raw)


class IdentityWorld:
    """Same response schedule feeds CLI-style and API-style observer adapters."""

    def __init__(self, monkeypatch, mode):
        self.calls = Counter()
        self.events = []
        self.after = lambda event: None
        self.before = lambda event: None
        self.now = 10.0
        self.document = {
            "Id": CID,
            "Image": IMAGE,
            "State": {"Running": True, "Status": "running", "Pid": 100},
        }
        self.hosts = {
            100: (100, 10, "0::/unit", 1, (0,) * 4, (0,) * 4),
            101: (101, 11, "0::/unit", 100, (65533,) * 4, (65533,) * 4),
            102: (102, 12, "0::/unit", 101, (65533,) * 4, (65533,) * 4),
        }
        self.namespaces = dict.fromkeys(self.hosts, 42)
        self.credentials = (102, 65533, 65533)
        self.top_override = None
        monkeypatch.setattr(observer, "_host_snapshot", self.snapshot)
        monkeypatch.setattr(observer, "time", SimpleNamespace(monotonic=self.clock))
        monkeypatch.setattr(peer_module, "_credentials", self.credential)
        monkeypatch.setattr(transport, "_get", self.get)
        monkeypatch.setattr(
            transport, "time", SimpleNamespace(monotonic=lambda: self.now)
        )

        def factory(image):
            self.calls["factory"] += 1
            assert image == IMAGE
            return (
                self
                if mode == "legacy"
                else transport.DockerReadTransport(image, deadline=60.0)
            )

        monkeypatch.setattr(observer, "DockerSupervisor", factory)
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
            profile=peer_module.PROFILE,
            input_sha256="c" * 64,
            reservation_id=uuid4(),
            image_id=IMAGE,
            ipc=HostTicketIPC(
                ipc_policy="host_ticket",
                ticket_sha256=hashlib.sha256(("a" * 64).encode()).hexdigest(),
            ),
        )
        values = {
            pid: KernelIdentity(
                host[0], host[1], "pid:[42]", host[2], host[3], host[4], host[5]
            )
            for pid, host in self.hosts.items()
        }
        control = peer_module.ControllerBinding(
            CID,
            hashlib.sha256(declaration_bytes(admission)).hexdigest(),
            values[100],
            values[101],
        )
        self.binding = peer_module.WorkerBinding(
            control,
            uuid4(),
            uuid4(),
            hashlib.sha256(("b" * 64).encode()).hexdigest(),
            values[102],
        )
        self.context = observer.DaemonIdentityContext(CID, IMAGE, 100, 60.0)
        self.peer = peer_module.Peer(
            admission, self.binding, self.context, object(), peer_module._ISSUER
        )

    def event(self, kind, getter, suffix=""):
        name = f"{kind}{self.calls[kind]}{suffix}"
        self.calls[kind] += 1
        self.events.append(name)
        self.before(name)
        value = copy.deepcopy(getter())
        self.after(name)
        return value

    def _inspect(self, cid):
        assert cid == CID
        return self.event("inspect", lambda: self.document)

    def top(self):
        return self.event(
            "top",
            lambda: (
                self.top_override
                if self.top_override is not None
                else {
                    "Titles": TITLES,
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
                }
            ),
        )

    def _command(self, args):
        assert args == TOP_ARGS
        value = self.top()
        raw = (
            "\n".join(" ".join(row) for row in [value["Titles"], *value["Processes"]])
            + "\n"
        ).encode("ascii")
        return 0, raw

    def get(self, path, deadline):
        assert deadline == self.context.deadline == 60.0
        if path == f"{transport.API}/containers/{CID}/json":
            return self._inspect(CID)
        assert (
            path
            == f"{transport.API}/containers/{CID}/top?ps_args=-eo%20pid%2Cuid%2Cppid%2Cpidns%2Ccomm"
        )
        return self.top()

    def snapshot(self, pid):
        return self.event("host", lambda: self.hosts[pid], f"-{pid}")

    def clock(self):
        self.calls["clock"] += 1
        return self.now

    def credential(self, channel):
        return self.event("credentials", lambda: self.credentials)

    def mutate(self, pid, field):
        if field == "namespace":
            self.namespaces[pid] += 1
            return
        if field == "missing":
            del self.hosts[pid]
            return
        host = list(self.hosts[pid])
        index = {"start": 1, "cgroup": 2, "ppid": 3, "uids": 4, "gids": 5}[field]
        host[index] = (
            host[index] + "/other"
            if field == "cgroup"
            else ((12345,) * 4 if field in ("uids", "gids") else host[index] + 1)
        )
        self.hosts[pid] = tuple(host)

    def outcome(self):
        try:
            peer_module._revalidate(self.peer)
        except ProcessError as error:
            return error.code
        return "accepted"


EVENTS = [
    "inspect0",
    "host0-100",
    "host1-101",
    "top0",
    "host2-100",
    "host3-101",
    "inspect1",
    "inspect2",
    "host4-100",
    "host5-102",
    "top1",
    "host6-100",
    "host7-102",
    "inspect3",
    "inspect4",
    "host8-100",
    "host9-101",
    "top2",
    "host10-100",
    "host11-101",
    "inspect5",
    "credentials0",
]


@pytest.mark.parametrize("mode", ["legacy", "api"])
def test_original_observer_all_nine_calls_and_host_order_unchanged(monkeypatch, mode):
    world = IdentityWorld(monkeypatch, mode)
    assert world.outcome() == "accepted"
    assert world.events == EVENTS
    assert world.calls == Counter(
        inspect=6, top=3, host=12, clock=12, factory=3, credentials=1
    )
    assert world.outcome() == "accepted"
    assert {
        key: world.calls[key]
        for key in ("inspect", "top", "host", "clock", "credentials")
    } == {"inspect": 12, "top": 6, "host": 24, "clock": 24, "credentials": 2}


@pytest.mark.parametrize("seam", EVENTS[:-1])
@pytest.mark.parametrize("pid", [100, 101, 102])
@pytest.mark.parametrize(
    "field", ["start", "cgroup", "ppid", "uids", "gids", "namespace", "missing"]
)
def test_identity_mutation_at_every_original_seam_has_same_result(
    monkeypatch, seam, pid, field
):
    results = []
    for mode in ("legacy", "api"):
        world = IdentityWorld(monkeypatch, mode)
        world.after = lambda event, world=world: (
            world.mutate(pid, field) if event == seam else None
        )
        results.append((world.outcome(), world.events))
    # Late changes after a field's final read can be accepted by both designs;
    # this intentionally proves equal traces, not continuous kernel truth.
    assert results[0] == results[1]


@pytest.mark.parametrize("seam", EVENTS[:-1])
@pytest.mark.parametrize("field", ["Id", "Image", "Pid", "Running", "Status"])
def test_container_change_at_every_seam_has_same_result(monkeypatch, seam, field):
    results = []
    for mode in ("legacy", "api"):
        world = IdentityWorld(monkeypatch, mode)

        def change(event, world=world):
            if event == seam:
                target = (
                    world.document
                    if field in ("Id", "Image")
                    else world.document["State"]
                )
                target[field] = {
                    "Id": "f" * 64,
                    "Image": "sha256:" + "f" * 64,
                    "Pid": 999,
                    "Running": False,
                    "Status": "paused",
                }[field]

        world.after = change
        results.append((world.outcome(), world.events))
    assert results[0] == results[1]


@pytest.mark.parametrize("seam", EVENTS[:-1])
def test_original_deadline_expiry_at_every_seam_same_rejection(monkeypatch, seam):
    results = []
    for mode in ("legacy", "api"):
        world = IdentityWorld(monkeypatch, mode)
        world.after = lambda event, world=world: (
            setattr(world, "now", 60.0) if event == seam else None
        )
        outcome = world.outcome()
        assert outcome != "accepted"
        assert world.calls["credentials"] == 0
        assert world.context.deadline == 60.0
        results.append((outcome, world.events))
    assert results[0] == results[1]


@pytest.mark.parametrize("mode", ["legacy", "api"])
def test_transient_pause_counterexample_is_not_lost(monkeypatch, mode):
    world = IdentityWorld(monkeypatch, mode)
    world.after = lambda event: (
        world.document["State"].update(Status="paused") if event == "top0" else None
    )
    world.before = lambda event: (
        world.document["State"].update(Status="running") if event == "top1" else None
    )
    assert world.outcome() != "accepted"
    assert world.events[-1] == "inspect1"
    assert world.calls["top"] == 1


@pytest.mark.parametrize("mode", ["legacy", "api"])
def test_namespace_only_controller_drift_keeps_final_pair_check(monkeypatch, mode):
    world = IdentityWorld(monkeypatch, mode)
    old_hosts = copy.deepcopy(world.hosts)
    world.after = lambda event: (
        world.mutate(101, "namespace") if event == "top0" else None
    )
    assert world.outcome() != "accepted"
    assert world.hosts == old_hosts
    assert world.calls["top"] == 3
    assert world.calls["inspect"] == 6


@pytest.mark.parametrize("mode", ["legacy", "api"])
@pytest.mark.parametrize(
    "credentials",
    [(101, 65533, 65533), (103, 65533, 65533), (102, 0, 65533), (102, 65533, 0)],
)
def test_socket_credential_comparison_unchanged(monkeypatch, mode, credentials):
    world = IdentityWorld(monkeypatch, mode)
    world.credentials = credentials
    assert world.outcome() == "oe_structured_protected_peer"
    assert world.events == EVENTS


@pytest.mark.parametrize("mode", ["legacy", "api"])
def test_next_revalidation_never_reuses_cached_worker_observation(monkeypatch, mode):
    world = IdentityWorld(monkeypatch, mode)
    assert world.outcome() == "accepted"
    world.mutate(102, "start")
    assert world.outcome() != "accepted"
    assert world.calls["top"] == 5
    assert world.calls["inspect"] == 10


@pytest.mark.parametrize(
    "field,value",
    [
        ("container_id", "f" * 64),
        ("image_id", "sha256:" + "f" * 64),
        ("leader_pid", 999),
    ],
)
def test_mismatched_trusted_context_fails_before_any_transport(
    monkeypatch, field, value
):
    world = IdentityWorld(monkeypatch, "api")
    world.peer = replace(world.peer, _context=replace(world.context, **{field: value}))
    assert world.outcome() == "oe_structured_protected_peer"
    assert world.events == []


@pytest.mark.parametrize("sample", [0, 1, 2])
@pytest.mark.parametrize("pid", [100, 101, 102])
@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate", "uid", "ppid", "namespace"]
)
def test_daemon_row_mutations_at_each_original_top_same_observation_result(
    monkeypatch, sample, pid, mutation
):
    results = []
    for mode in ("legacy", "api"):
        world = IdentityWorld(monkeypatch, mode)

        def change(event, world=world):
            if event != f"top{sample}":
                return
            value = copy.deepcopy(TOP_VALUE)
            if mutation == "missing":
                value["Processes"] = [
                    row for row in value["Processes"] if int(row[0]) != pid
                ]
            else:
                row = next(row for row in value["Processes"] if int(row[0]) == pid)
                if mutation == "duplicate":
                    value["Processes"].append(row.copy())
                else:
                    index = {"uid": 1, "ppid": 2, "namespace": 3}[mutation]
                    row[index] = str(int(row[index]) + 1)
            world.top_override = value

        world.before = change
        results.append((world.outcome(), world.events))
    assert results[0] == results[1]


@pytest.mark.parametrize(
    "seam", [event for event in EVENTS if event.startswith(("inspect", "top"))]
)
@pytest.mark.parametrize(
    "exception", [OSError, ValueError, TypeError, KeyError, IndexError]
)
def test_failed_observation_is_not_retried_or_replaced(monkeypatch, seam, exception):
    results = []
    for mode in ("legacy", "api"):
        world = IdentityWorld(monkeypatch, mode)

        def fail(event):
            if event == seam:
                raise exception("synthetic failure")

        world.before = fail
        outcome = world.outcome()
        assert outcome == "oe_controller_daemon_identity"
        assert world.events[-1] == seam
        assert world.calls["credentials"] == 0
        results.append((outcome, world.events))
    assert results[0] == results[1]


@pytest.mark.parametrize("mode", ["legacy", "api"])
@pytest.mark.parametrize("now", [60.0, 61.0, -541.0, float("nan")])
def test_observer_original_deadline_window_before_any_read(monkeypatch, mode, now):
    world = IdentityWorld(monkeypatch, mode)
    world.now = now
    assert world.outcome() == "oe_controller_daemon_identity"
    assert world.events == []
