"""Synthetic shared transport tests: in-memory sockets, never Docker/network.

These verify fail-closed restrictions and mapped error categories, not complete
CLI acceptance parity, real-daemon compatibility, or physical elapsed latency.
"""

import importlib
import io
import json
import socket
import stat
import struct
from types import SimpleNamespace

import pytest

from packages.research_runtime.process_contracts import ProcessError
from runtimes.research_supervisor import docker_observation_transport as m

CID = "a" * 64
IMAGE = "sha256:" + "b" * 64
PATH = f"{m.API}/containers/{CID}/json"
TOP_PATH = f"{m.API}/containers/{CID}/top?ps_args=-eo%20pid%2Cuid%2Cppid%2Cpidns%2Ccomm"
ARGS = ["top", CID, "-eo", m.TOP_FIELDS]
TITLES = ["PID", "UID", "PPID", "PIDNS", "COMMAND"]
RD = "rd_controller_daemon_identity"
OE = "oe_controller_daemon_identity"
UNCERTAIN = "research_process_uncertain"
OUTPUT = "research_process_output"


def response(body=b'{"Id":"ok"}', headers=None, status=b"200 OK"):
    if headers is None:
        headers = (
            b"Content-Type: application/json\r\nContent-Length: "
            + str(len(body)).encode()
        )
    return b"HTTP/1.1 " + status + b"\r\n" + headers + b"\r\n\r\n" + body


def encoded(value):
    return json.dumps(value, separators=(",", ":")).encode()


def top(rows=None):
    return {
        "Titles": TITLES.copy(),
        "Processes": rows if rows is not None else [["100", "0", "1", "42", "python"]],
    }


def assert_code(code, action):
    with pytest.raises(ProcessError) as caught:
        action()
    assert caught.value.code == code
    assert str(caught.value) == code


class Connection:
    def __init__(self, raw):
        self.raw = io.BytesIO(raw)
        self.closed = False
        self.requests = []
        self.timeouts = []
        self.peer = (42, 0, 0)
        self.connected = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def settimeout(self, timeout):
        assert 0 < timeout <= 10
        self.timeouts.append(timeout)

    def connect(self, path):
        assert path == "/var/run/docker.sock"
        self.connected = True

    def getsockopt(self, level, option, size):
        assert (level, option, size) == (socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        return struct.pack("3i", *self.peer)

    def sendall(self, raw):
        self.requests.append(raw)

    def recv_into(self, target):
        return self.raw.readinto(target)


@pytest.fixture(autouse=True)
def forbid_real_socket(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Shared transport tests must not open real sockets")

    monkeypatch.setattr(m.socket, "socket", forbidden)


@pytest.fixture(params=[RD, OE], ids=["rd", "oe"])
def family(request):
    return request.param


@pytest.fixture
def wire(monkeypatch, family):
    clock = [100.0]
    connections = []
    creations = []
    info = SimpleNamespace(
        st_mode=stat.S_IFSOCK | 0o660, st_uid=0, st_gid=999, st_dev=1, st_ino=2
    )
    monkeypatch.setattr(m.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(m.os, "lstat", lambda path: info)

    def install(raw):
        connection = Connection(raw)
        connections.append(connection)

        def create(*args):
            assert args == (socket.AF_UNIX, socket.SOCK_STREAM)
            creations.append(connection)
            return connection

        monkeypatch.setattr(m.socket, "socket", create)
        return connection

    return SimpleNamespace(
        install=install,
        clock=clock,
        info=info,
        connections=connections,
        creations=creations,
        code=family,
        budget=lambda deadline=108: m._Budget(deadline, family),
        client=lambda deadline=108: m.DockerObservationTransport(
            IMAGE, deadline=deadline, identity_code=family
        ),
    )


def test_fixed_get_and_close_without_environment_configuration(wire, monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "tcp://untrusted.invalid:2375")
    monkeypatch.setenv("DOCKER_API_VERSION", "1.99")
    connection = wire.install(response())
    assert m._get(PATH, wire.budget()) == {"Id": "ok"}
    assert connection.closed
    assert connection.requests == [
        (
            f"GET {PATH} HTTP/1.1\r\nHost: docker\r\nConnection: close\r\nAccept: application/json\r\n\r\n"
        ).encode()
    ]


def test_chunked_response(wire):
    connection = wire.install(
        response(
            b'B\r\n{"Id":"ok"}\r\n0\r\n\r\n',
            b"Content-Type: application/json\r\nTransfer-Encoding: chunked",
        )
    )
    assert m._get(PATH, wire.budget()) == {"Id": "ok"}
    assert connection.closed


@pytest.mark.parametrize(
    "raw,code",
    [
        (response(status=b"301 Redirect"), UNCERTAIN),
        (response(status=b"404 Not Found"), UNCERTAIN),
        (response(status=b"500 Error"), UNCERTAIN),
        (
            response(b"{}", b"Content-Type: application/json\r\nContent-Length: 3"),
            UNCERTAIN,
        ),
        (
            response(
                b"{}",
                b"Content-Type: application/json\r\nContent-Length: 2\r\nContent-Length: 2",
            ),
            UNCERTAIN,
        ),
        (
            response(
                b"{}",
                b"Content-Type: application/json\r\nContent-Length: 2\r\nTransfer-Encoding: chunked",
            ),
            UNCERTAIN,
        ),
        (
            response(
                b"{}", b"Content-Type: application/json\r\nTransfer-Encoding: gzip"
            ),
            UNCERTAIN,
        ),
        (response(b"{}", b"Content-Type: text/plain\r\nContent-Length: 2"), UNCERTAIN),
        (
            response(
                b"{}",
                b"Content-Type: application/json\r\nContent-Length: 2\r\nContent-Encoding: gzip",
            ),
            UNCERTAIN,
        ),
        (response(b"{}", b"Content-Type: application/json"), UNCERTAIN),
        (
            response(b"{}", b"Content-Type: application/json\r\nContent-Length: -1"),
            UNCERTAIN,
        ),
        (
            response(b"{}", b"Content-Type: application/json\r\nContent-Length: 65537"),
            OUTPUT,
        ),
        (
            response(
                b"{}", b"Content-Type: application/json\r\nX-Big: " + b"x" * 90000
            ),
            UNCERTAIN,
        ),
        (response(b'{"a":1,"a":2}'), UNCERTAIN),
        (response(b'{"a":NaN}'), UNCERTAIN),
        (response(b'{"a":Infinity}'), UNCERTAIN),
        (response(b"[]"), UNCERTAIN),
        (response(b"1"), UNCERTAIN),
        (response(b"true"), UNCERTAIN),
        (response(b'"string"'), UNCERTAIN),
        (response(b"{}junk"), UNCERTAIN),
        (response(b"\xff"), UNCERTAIN),
        (response(b""), UNCERTAIN),
        (
            response(
                b"1\r\n{\r\n0\r\n\r\n",
                b"Content-Type: application/json\r\nTransfer-Encoding: chunked",
            ),
            UNCERTAIN,
        ),
    ],
)
def test_invalid_inspect_response_exact_category_and_close(wire, raw, code):
    connection = wire.install(raw)
    assert_code(code, lambda: m._get(PATH, wire.budget()))
    assert connection.closed


def test_null_inspect_retains_raw_null_semantics_and_family_rejection(
    wire, monkeypatch
):
    # v27.5.1 ContainerInspectWithRaw decodes null into a zero-value struct and
    # retains raw null. inspector.go wraps it as [null], so legacy _inspect
    # returns None and the unchanged observation check rejects with its family.
    # https://raw.githubusercontent.com/docker/cli/v27.5.1/vendor/github.com/docker/docker/client/container_inspect.go
    # https://raw.githubusercontent.com/docker/cli/v27.5.1/cli/command/inspect/inspector.go
    bounds = []
    original = m._inspect_render_bound

    def record(raw):
        bounds.append(raw)
        return original(raw)

    monkeypatch.setattr(m, "_inspect_render_bound", record)
    first = wire.install(response(b"null"))
    assert wire.client()._inspect(CID) is None
    assert first.closed and bounds == [b"null"]
    module = importlib.import_module(
        "runtimes.rdagent_operational.daemon_identity"
        if wire.code == RD
        else "runtimes.openevolve_operational.daemon_identity"
    )
    second = wire.install(response(b"null"))
    assert_code(wire.code, lambda: module._inspect(wire.client(), CID, IMAGE, 100))
    assert second.closed and bounds == [b"null", b"null"]


@pytest.mark.parametrize("prefix", ["No such container:", "No such object:"])
def test_missing_inspect_returns_none_but_top_remains_uncertain(wire, prefix):
    raw = response(encoded({"message": f"{prefix} {CID}"}), status=b"404 Not Found")
    first = wire.install(raw)
    assert wire.client()._inspect(CID) is None
    assert first.closed
    second = wire.install(raw)
    assert_code(UNCERTAIN, lambda: wire.client()._command(ARGS))
    assert second.closed


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"{}",
        b"[]",
        b"null",
        b"bad",
        b'{"message":null}',
        b'{"message":"API version not supported"}',
    ],
)
def test_unverified_404_never_means_missing(wire, body):
    connection = wire.install(response(body, status=b"404 Not Found"))
    assert_code(UNCERTAIN, lambda: wire.client()._inspect(CID))
    assert connection.closed


@pytest.mark.parametrize("body", [b"", b"bad", b"[]", b"\xff", b'{"a":1,"a":2}'])
def test_invalid_top_json_is_command_failure_not_inspect_decode(wire, body):
    wire.install(response(body))
    assert_code(UNCERTAIN, lambda: wire.client()._command(ARGS))


@pytest.mark.parametrize(
    "path",
    [
        "http://evil/json",
        "/version",
        "/v1.45/containers/a/kill",
        PATH + "?x=1",
        PATH.replace(CID, "../escape"),
        PATH.replace("v1.45", "v1.47"),
    ],
)
def test_path_allowlist_before_connection(wire, path):
    connection = wire.install(response())
    assert_code(UNCERTAIN, lambda: m._get(path, wire.budget()))
    assert not wire.creations and not connection.requests


@pytest.mark.parametrize(
    "change",
    ["uid", "world", "symlink", "peer", "peer_pid", "inode_before", "inode_after"],
)
def test_socket_identity_failures_are_uncertain(wire, change):
    connection = wire.install(response())
    if change == "uid":
        wire.info.st_uid = 1000
    elif change == "world":
        wire.info.st_mode |= 0o006
    elif change == "symlink":
        wire.info.st_mode = stat.S_IFLNK | 0o777
    elif change == "peer":
        connection.peer = (42, 1000, 1000)
    elif change == "peer_pid":
        connection.peer = (0, 0, 0)
    else:
        method = "connect" if change == "inode_before" else "sendall"
        old = getattr(connection, method)

        def swap(*args):
            old(*args)
            wire.info.st_ino += 1

        setattr(connection, method, swap)
    assert_code(UNCERTAIN, lambda: m._get(PATH, wire.budget()))
    if wire.creations:
        assert connection.closed
    if change != "inode_after":
        assert not connection.requests


@pytest.mark.parametrize("method", ["connect", "getsockopt", "sendall", "recv_into"])
def test_io_errors_close_without_fallback_or_private_details(wire, method):
    connection = wire.install(response())

    def denied(*args):
        raise PermissionError("private failure detail")

    setattr(connection, method, denied)
    assert_code(UNCERTAIN, lambda: m._get(PATH, wire.budget()))
    assert connection.closed and len(wire.creations) == 1


def test_missing_socket_is_uncertain_before_connection(wire, monkeypatch):
    wire.install(response())

    def missing(_):
        raise FileNotFoundError("private endpoint detail")

    monkeypatch.setattr(m.os, "lstat", missing)
    assert_code(UNCERTAIN, lambda: m._get(PATH, wire.budget()))
    assert not wire.creations


@pytest.mark.parametrize("deadline", [100.0, 99.0])
def test_original_deadline_expired_before_any_connection(wire, deadline):
    connection = wire.install(response())
    assert_code(wire.code, lambda: wire.client(deadline)._inspect(CID))
    assert not wire.creations and not connection.requests


@pytest.mark.parametrize(
    "deadline,finish,expected", [(108, 108, "family"), (130, 110, UNCERTAIN)]
)
def test_original_deadline_vs_ten_second_io_timeout(wire, deadline, finish, expected):
    connection = wire.install(response())

    def timeout(_):
        wire.clock[0] = finish
        raise TimeoutError("private timeout detail")

    connection.recv_into = timeout
    assert_code(
        wire.code if expected == "family" else expected,
        lambda: wire.client(deadline)._inspect(CID),
    )
    assert connection.closed
    assert connection.timeouts[0] == min(10, deadline - 100)


@pytest.mark.parametrize("deadline,expected", [(108, "family"), (130, UNCERTAIN)])
def test_trickle_cannot_reset_absolute_deadlines(wire, deadline, expected):
    connection = wire.install(response())
    old = connection.recv_into

    def slow(target):
        result = old(target[:1])
        wire.clock[0] += 1
        return result

    connection.recv_into = slow
    assert_code(
        wire.code if expected == "family" else expected,
        lambda: wire.client(deadline)._inspect(CID),
    )
    assert connection.closed and wire.clock[0] <= min(deadline, 110)


@pytest.mark.parametrize(
    "deadline,finish,expected", [(108, 109, "family"), (130, 111, UNCERTAIN)]
)
@pytest.mark.parametrize("body", [b'{"Id":"ok"}', b"null"])
def test_parse_completion_must_meet_both_deadlines(
    wire, monkeypatch, deadline, finish, expected, body
):
    connection = wire.install(response(body))
    original = m.json.loads

    def delayed(*args, **kwargs):
        value = original(*args, **kwargs)
        wire.clock[0] = finish
        return value

    monkeypatch.setattr(m.json, "loads", delayed)
    assert_code(
        wire.code if expected == "family" else expected,
        lambda: wire.client(deadline)._inspect(CID),
    )
    assert connection.closed


def test_fresh_calls_keep_original_deadline_without_reusing_connection(wire):
    client = wire.client(130)
    first = wire.install(response())
    assert client._inspect(CID) == {"Id": "ok"}
    wire.clock[0] = 105
    second = wire.install(response())
    assert client._inspect(CID) == {"Id": "ok"}
    assert client.deadline == 130
    assert first is not second and first.closed and second.closed
    assert len(first.requests) == len(second.requests) == 1
    assert first.timeouts[0] == second.timeouts[0] == 10


@pytest.mark.parametrize(
    "chunk",
    [
        b"2\r\n{}XX0\r\n\r\n",
        b"2\r\n{}\r\n0\r\n",
        b"+2\r\n{}\r\n0\r\n\r\n",
        b"2\n{}\r\n0\r\n\r\n",
        b"2\r\n{}\r\n0\r\nContent-Length: 0\r\n\r\n",
        b"2;extension=1\r\n{}\r\n0\r\n\r\n",
    ],
)
def test_strict_chunk_framing(wire, chunk):
    connection = wire.install(
        response(chunk, b"Content-Type: application/json\r\nTransfer-Encoding: chunked")
    )
    assert_code(UNCERTAIN, lambda: m._get(PATH, wire.budget()))
    assert connection.closed


@pytest.mark.parametrize(
    "raw",
    [
        response(b"{}") + b"ignored",
        response(b"{}").replace(b"HTTP/1.1", b"HTTP/1.9"),
        response(b"{}").replace(b"\r\n", b"\n"),
        response(b"{}").replace(b"Content-Length", b" Content-Length"),
    ],
)
def test_strict_status_headers_and_eof(wire, raw):
    connection = wire.install(raw)
    assert_code(UNCERTAIN, lambda: m._get(PATH, wire.budget()))
    assert connection.closed


def test_wire_limit_is_uncertain_not_cli_output_limit(wire, monkeypatch):
    raw = response()
    monkeypatch.setattr(m, "MAX_WIRE", len(raw) - 1)
    connection = wire.install(raw)
    assert_code(UNCERTAIN, lambda: m._get(PATH, wire.budget()))
    assert connection.closed


def test_exact_wire_limit_is_inclusive(wire, monkeypatch):
    raw = response()
    monkeypatch.setattr(m, "MAX_WIRE", len(raw))
    connection = wire.install(raw)
    assert m._get(PATH, wire.budget()) == {"Id": "ok"}
    assert connection.closed


def test_raw_body_limit_is_inclusive_before_operation_projection(wire):
    body = b'{"x":"' + b"x" * (m.MAX_BODY - 8) + b'"}'
    assert len(body) == m.MAX_BODY
    first = wire.install(response(body))
    assert len(m._get(TOP_PATH, wire.budget())["x"]) == m.MAX_BODY - 8
    assert first.closed
    second = wire.install(response(body + b" "))
    assert_code(OUTPUT, lambda: m._get(TOP_PATH, wire.budget()))
    assert second.closed


@pytest.mark.parametrize("chunks", [[65537], [40000, 25537]])
def test_chunked_body_limit_is_research_output(wire, chunks):
    body = (
        b"".join(f"{size:x}\r\n".encode() + b"x" * size + b"\r\n" for size in chunks)
        + b"0\r\n\r\n"
    )
    connection = wire.install(
        response(body, b"Content-Type: application/json\r\nTransfer-Encoding: chunked")
    )
    assert_code(OUTPUT, lambda: m._get(PATH, wire.budget()))
    assert connection.closed


def test_inspect_conservative_size_is_stricter_not_byte_equivalent(wire):
    payload = {"items": [[]] * 2000}
    raw = encoded(payload)
    assert len((json.dumps([payload], indent=4) + "\n").encode()) <= m.MAX_BODY
    wire.install(response(raw))
    assert_code(OUTPUT, lambda: wire.client()._inspect(CID))


def test_inspect_conservative_bound_exact_edge(monkeypatch):
    raw = b'{"value":"x"}'
    bound = len(raw) + 43
    monkeypatch.setattr(m, "MAX_BODY", bound)
    m._inspect_render_bound(raw)
    monkeypatch.setattr(m, "MAX_BODY", bound - 1)
    assert_code(OUTPUT, lambda: m._inspect_render_bound(raw))


def test_top_fixed_route_and_cli_padding(wire):
    value = top()
    connection = wire.install(response(encoded(value)))
    code, raw = wire.client()._command(ARGS)
    assert code == 0
    assert raw == b"".join(
        "".join(row[i].ljust(20) for i in range(4)).encode() + row[4].encode() + b"\n"
        for row in [TITLES, *value["Processes"]]
    )
    assert connection.requests[0].startswith(f"GET {TOP_PATH} HTTP/1.1\r\n".encode())


def test_top_rendered_output_limit_inclusive(wire, monkeypatch):
    body = encoded(top())
    wire.install(response(body))
    raw = wire.client()._command(ARGS)[1]
    assert len(body) < len(raw)
    monkeypatch.setattr(m, "MAX_BODY", len(raw))
    wire.install(response(body))
    assert wire.client()._command(ARGS)[1] == raw
    monkeypatch.setattr(m, "MAX_BODY", len(raw) - 1)
    wire.install(response(body))
    assert_code(OUTPUT, lambda: wire.client()._command(ARGS))


@pytest.mark.parametrize(
    "name", ["Content-Length", "Content-Type", "Transfer-Encoding", "Content-Encoding"]
)
def test_duplicate_security_relevant_headers_rejected(wire, name):
    values = {
        "Content-Length": "2",
        "Content-Type": "application/json",
        "Transfer-Encoding": "chunked",
        "Content-Encoding": "identity",
    }
    header = f"{name}: {values[name]}\r\n{name}: {values[name]}".encode()
    if name != "Content-Type":
        header = b"Content-Type: application/json\r\n" + header
    if name not in {"Content-Length", "Transfer-Encoding"}:
        header += b"\r\nContent-Length: 2"
    connection = wire.install(response(b"{}", header))
    assert_code(UNCERTAIN, lambda: m._get(PATH, wire.budget()))
    assert connection.closed


@pytest.mark.parametrize(
    "value",
    [
        {"Titles": TITLES, "Processes": 123},
        {"Titles": TITLES, "Processes": {}},
        {"Titles": TITLES, "Processes": "rows"},
        {"Titles": TITLES, "Processes": [123]},
        {"Titles": TITLES, "Processes": ["row"]},
        {"Titles": TITLES, "Processes": [{}]},
        top([[100, "0", "1", "42", "p"]]),
        top([[True, "0", "1", "42", "p"]]),
        top([[{}, "0", "1", "42", "p"]]),
        top([[[], "0", "1", "42", "p"]]),
        {"Titles": 123, "Processes": []},
        {"Titles": [123], "Processes": []},
        {"Titles": [True], "Processes": []},
        {"Titles": "PID UID PPID PIDNS COMMAND", "Processes": []},
    ],
)
def test_top_api_schema_failures_are_uncertain(wire, value):
    wire.install(response(encoded(value)))
    assert_code(UNCERTAIN, lambda: wire.client()._command(ARGS))


# Go's typed ContainerTop decoder uses []string and [][]string. Missing/null
# slices default to nil, null primitive string cells stay empty, and unknown
# keys are ignored. These cases decode successfully, then fail observation.
# Sources inspected for these expectations (not real-daemon execution):
# https://raw.githubusercontent.com/moby/moby/master/client/container_top.go
# https://raw.githubusercontent.com/docker/cli/v27.5.1/cli/command/container/top.go
# https://raw.githubusercontent.com/golang/go/go1.22.3/src/encoding/json/decode.go
@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"Unknown": 123},
        {"Titles": TITLES},
        {"Processes": [["100", "0", "1", "42", "p"]]},
        {"Titles": None, "Processes": []},
        {"Titles": TITLES, "Processes": None},
        {"Titles": None, "Processes": None},
        {"Titles": [None], "Processes": []},
        top([None]),
        top([[None, "0", "1", "42", "p"]]),
        top([["100", "0", "1", "42", None]]),
    ],
)
def test_top_go_null_and_missing_defaults_are_family_failures(wire, value):
    wire.install(response(encoded(value)))
    assert_code(wire.code, lambda: wire.client()._command(ARGS))


@pytest.mark.parametrize("unknown", [None, 123, [], {"nested": [1, 2, 3]}])
def test_top_go_unknown_fields_do_not_change_valid_inventory(wire, unknown):
    value = top()
    wire.install(response(encoded(value)))
    expected = wire.client()._command(ARGS)
    value["Unrelated"] = unknown
    wire.install(response(encoded(value)))
    assert wire.client()._command(ARGS) == expected


@pytest.mark.parametrize(
    "titles,processes",
    [("titles", "processes"), ("TITLES", "PROCESSES"), ("tItLeS", "pRoCeSsEs")],
)
def test_top_go_unambiguous_ascii_field_aliases(wire, titles, processes):
    value = top()
    wire.install(response(encoded(value)))
    expected = wire.client()._command(ARGS)
    renamed = {titles: value["Titles"], processes: value["Processes"]}
    wire.install(response(encoded(renamed)))
    assert wire.client()._command(ARGS) == expected


@pytest.mark.parametrize("field", ["Titles", "Processes"])
def test_top_conflicting_case_aliases_remain_explicitly_stricter(wire, field):
    value = top()
    value[field.lower()] = value[field]
    wire.install(response(encoded(value)))
    # Do not silently pick a duplicate authority-bearing field. This restriction
    # is intentionally stricter than Go's decoded field replacement behavior.
    assert_code(UNCERTAIN, lambda: wire.client()._command(ARGS))


@pytest.mark.parametrize(
    "value",
    [
        {"Titles": ["WRONG"], "Processes": [[123]]},
        {"Titles": None, "Processes": [[True]]},
        {"Processes": [[123]]},
        {"Titles": [123], "Processes": None},
        top([["short"], [123]]),
        top([None, [True]]),
    ],
)
def test_top_go_type_errors_precede_semantic_inventory_errors(wire, value):
    wire.install(response(encoded(value)))
    assert_code(UNCERTAIN, lambda: wire.client()._command(ARGS))


@pytest.mark.parametrize(
    "mutation", ["titles", "empty", "rows", "short", "space", "empty_cell", "unicode"]
)
def test_top_renderable_invalid_inventory_uses_family(wire, mutation):
    value = top()
    if mutation == "titles":
        value["Titles"][-1] = "CMD"
    elif mutation == "empty":
        value["Processes"] = []
    elif mutation == "rows":
        value["Processes"] *= 65
    elif mutation == "short":
        value["Processes"][0].pop()
    else:
        value["Processes"][0][-1] = {
            "space": "two words",
            "empty_cell": "",
            "unicode": "é",
        }[mutation]
    wire.install(response(encoded(value)))
    assert_code(wire.code, lambda: wire.client()._command(ARGS))


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "zero_namespace",
        "negative_pid",
        "nonnumeric_uid",
        "long_comm",
        "top_byte_limit",
    ],
)
def test_unchanged_family_parser_owns_semantic_inventory_rejection(wire, mutation):
    value = top()
    row = value["Processes"][0]
    if mutation == "duplicate":
        value["Processes"].append(row.copy())
    elif mutation == "zero_namespace":
        row[3] = "0"
    elif mutation == "negative_pid":
        row[0] = "-1"
    elif mutation == "nonnumeric_uid":
        row[1] = "root"
    elif mutation == "long_comm":
        row[4] = "p" * 65
    else:
        row[0] = "1" * 9000
    module = importlib.import_module(
        "runtimes.rdagent_operational.daemon_identity"
        if wire.code == RD
        else "runtimes.openevolve_operational.daemon_identity"
    )
    wire.install(response(encoded(value)))
    _, raw = wire.client()._command(ARGS)
    assert_code(wire.code, lambda: module.parse_top(raw))


def test_large_padded_top_is_output_before_parser(wire):
    value = top([["1" * 33000, "0", "1", "42", "p"]])
    assert len(encoded(value)) < m.MAX_BODY
    wire.install(response(encoded(value)))
    assert_code(OUTPUT, lambda: wire.client()._command(ARGS))


def test_oversized_top_inventory_retains_cli_output_error_priority(wire):
    value = top([[str(pid), "0", "1", "42", "p"] for pid in range(1, 1001)])
    assert len(encoded(value)) < m.MAX_BODY
    assert (len(value["Processes"]) + 1) * 82 > m.MAX_BODY
    wire.install(response(encoded(value)))
    assert_code(OUTPUT, lambda: wire.client()._command(ARGS))


@pytest.mark.parametrize("image", ["latest", "sha256:" + "a" * 63, None, True])
def test_constructor_image_error_preserved(family, image):
    assert_code(
        "research_process_image",
        lambda: m.DockerObservationTransport(image, deadline=108, identity_code=family),
    )


@pytest.mark.parametrize("deadline", [True, None, float("nan"), float("inf")])
def test_constructor_invalid_deadline_uses_family(family, deadline):
    assert_code(
        family,
        lambda: m.DockerObservationTransport(
            IMAGE, deadline=deadline, identity_code=family
        ),
    )


@pytest.mark.parametrize(
    "code", ["arbitrary", "research_process_uncertain", None, True]
)
def test_constructor_only_accepts_closed_identity_families(code):
    assert_code(
        UNCERTAIN,
        lambda: m.DockerObservationTransport(IMAGE, deadline=108, identity_code=code),
    )


@pytest.mark.parametrize("cid", ["", "a" * 63, "A" * 64, CID + "/", True, None])
def test_inspect_invalid_id_rejected_before_get(wire, cid):
    wire.install(response())
    assert_code(wire.code, lambda: wire.client()._inspect(cid))
    assert not wire.creations


@pytest.mark.parametrize(
    "args",
    [
        None,
        (),
        ARGS + ["extra"],
        ["exec", CID, "-eo", m.TOP_FIELDS],
        ["top", "name", "-eo", m.TOP_FIELDS],
        ["top", CID, "-ef", m.TOP_FIELDS],
        ["top", CID, "-eo", "pid,comm"],
    ],
)
def test_no_operation_or_field_expansion_before_get(wire, args):
    wire.install(response())
    assert_code(wire.code, lambda: wire.client()._command(args))
    assert not wire.creations
