"""Offline lifecycle/privacy checks only; no real Docker, sockets, or timings."""

import copy
import importlib.util
import json
import socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from packages.research_runtime.process_contracts import ProcessError
from runtimes.research_supervisor.supervisor import DockerSupervisor

IMAGE_ID = "sha256:" + "b" * 64
CID = "c" * 64
TOKEN = "123456-2"
PREFIX = b"OE_DOCKER_TRANSPORT_PROOF_V1:"
SECRET = "synthetic-private-daemon-data"


@pytest.fixture
def proof(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline suite must not invoke Docker or sockets")

    monkeypatch.setattr(DockerSupervisor, "_command", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setenv("GITHUB_RUN_ID", "123456")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    path = Path(__file__).parents[2] / "script" / "oe_docker_transport_proof.py"
    spec = importlib.util.spec_from_file_location("oe_transport_proof_test", path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    monkeypatch.chdir(tmp_path)
    return value


def complete_report(proof):
    value = proof.empty_report()
    value.update(complete=True, cleanup=True, api_compatible=True, authority_equal=True)
    for mode in ("cli", "api"):
        value["samples"][mode] = [
            {
                "counts": {"inspect": 6, "top": 3, "host": 12},
                "seconds": 0.25,
                "error": None,
            }
            for _ in range(3)
        ]
    value["negatives"] = {
        name: {"cli": True, "api": True} for name in value["negatives"]
    }
    return value


def encoded(value):
    return PREFIX + json.dumps(value, separators=(",", ":")).encode() + b"\n"


def test_exact_owner_and_two_reserved_names(proof):
    assert proof.owner() == TOKEN
    assert proof.names(TOKEN) == [
        "mineagent-oe-transport-123456-2-normal",
        "mineagent-oe-transport-123456-2-oversize",
    ]


@pytest.mark.parametrize(
    "key,value",
    [
        ("GITHUB_RUN_ID", ""),
        ("GITHUB_RUN_ID", "../other"),
        ("GITHUB_RUN_ID", "1" * 21),
        ("GITHUB_RUN_ID", "123\n"),
        ("GITHUB_RUN_ATTEMPT", ""),
        ("GITHUB_RUN_ATTEMPT", "1" * 7),
        ("GITHUB_RUN_ATTEMPT", "1;anything"),
    ],
)
def test_owner_never_accepts_arbitrary_names(proof, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ProcessError):
        proof.owner()


def test_image_lookup_is_exact_prior_approved_ref(proof):
    cli = SimpleNamespace(_command=Mock(return_value=(0, (IMAGE_ID + "\n").encode())))
    assert proof.image_id(cli) == IMAGE_ID
    cli._command.assert_called_once_with(
        ["image", "inspect", "--format", "{{.Id}}", proof.IMAGE]
    )
    assert (
        proof.IMAGE
        == "python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e"
    )


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"sha256:short\n",
        b"latest\n",
        (IMAGE_ID + "\n" + IMAGE_ID).encode(),
        b"\xff",
    ],
)
def test_image_lookup_rejects_ambiguous_identity(proof, raw):
    with pytest.raises((ProcessError, UnicodeError)):
        proof.image_id(SimpleNamespace(_command=lambda args: (0, raw)))


def test_owner_projection_is_fixed_and_bounded(proof):
    cli = SimpleNamespace(
        _command=Mock(return_value=(0, f"{CID}|{IMAGE_ID}|{TOKEN}\n".encode()))
    )
    assert proof.owned(cli, proof.names(TOKEN)[0], IMAGE_ID, TOKEN) == CID
    args = cli._command.call_args.args[0]
    assert args[:3] == ["container", "inspect", "--format"]
    assert args[-1] == proof.names(TOKEN)[0]
    assert (
        args[3] == '{{.Id}}|{{.Image}}|{{index .Config.Labels "' + proof.LABEL + '"}}'
    )
    assert cli._command.call_args.kwargs == {"check": False}


@pytest.mark.parametrize(
    "raw",
    [
        f"short|{IMAGE_ID}|{TOKEN}",
        f"{CID}|sha256:{'d' * 64}|{TOKEN}",
        f"{CID}|{IMAGE_ID}|other",
        f"{CID}|{IMAGE_ID}|{TOKEN}|extra",
        f"{CID}|{IMAGE_ID}|{TOKEN}\n{CID}|{IMAGE_ID}|{TOKEN}",
    ],
)
def test_foreign_or_ambiguous_ownership_cannot_authorize_removal(proof, raw):
    calls = []
    proof.claim(proof.names(TOKEN)[0], TOKEN)

    def command(args, **kwargs):
        calls.append(args)
        return 0, raw.encode()

    with pytest.raises(ProcessError):
        proof.cleanup(
            SimpleNamespace(_command=command), proof.names(TOKEN)[0], IMAGE_ID, TOKEN
        )
    assert not any(args[0] == "rm" for args in calls)


@pytest.mark.parametrize(
    "code,raw,missing",
    [
        (1, b"No such container: fixed", True),
        (1, b"No such object: fixed", True),
        (1, b"permission denied", False),
        (2, b"No such container: fixed", False),
        (125, b"daemon failed", False),
    ],
)
def test_absence_only_from_exact_missing_response(proof, code, raw, missing):
    cli = SimpleNamespace(_command=lambda args, **kw: (code, raw))
    if missing:
        assert proof.owned(cli, proof.names(TOKEN)[0], IMAGE_ID, TOKEN) is None
    else:
        with pytest.raises(ProcessError):
            proof.owned(cli, proof.names(TOKEN)[0], IMAGE_ID, TOKEN)


@pytest.mark.parametrize("oversized", [False, True])
def test_launch_fixed_inert_restrictions_and_no_pull(proof, monkeypatch, oversized):
    name = proof.names(TOKEN)[int(oversized)]
    monkeypatch.setattr(proof, "owned", Mock(side_effect=[None, CID]))
    cli = SimpleNamespace(_command=Mock(return_value=(0, (CID + "\n").encode())))
    assert proof.launch(cli, name, IMAGE_ID, TOKEN, oversized=oversized) == CID
    args = cli._command.call_args.args[0]
    assert args[:2] == ["run", "--detach"]
    for flag, value in [
        ("--pull", "never"),
        ("--name", name),
        ("--network", "none"),
        ("--ipc", "none"),
        ("--cap-drop", "ALL"),
        ("--security-opt", "no-new-privileges"),
        ("--user", "65533:65533"),
        ("--pids-limit", "8"),
        ("--memory", "64m"),
        ("--memory-swap", "64m"),
        ("--cpus", "1"),
        ("--entrypoint", "python"),
    ]:
        assert args[args.index(flag) + 1] == value
    assert "--read-only" in args
    assert not set(args) & {
        "--privileged",
        "--mount",
        "--volume",
        "-v",
        "--publish",
        "-p",
        "--pid",
        "--device",
        "--cap-add",
        "--gpus",
    }
    assert args[-5:] == [IMAGE_ID, "-I", "-S", "-c", proof.SLEEPER]
    labels = [args[index + 1] for index, arg in enumerate(args) if arg == "--label"]
    assert proof.LABEL + "=" + TOKEN in labels
    assert len(labels) == 1 + int(oversized)
    if oversized:
        assert labels[1] == "io.mineagent.proof-padding=" + "x" * 65536
    assert "range(2)" in proof.SLEEPER and "time.sleep(180)" in proof.SLEEPER


def test_launch_refuses_preexisting_name_without_lifecycle_action(proof, monkeypatch):
    monkeypatch.setattr(proof, "owned", lambda *args: CID)
    cli = SimpleNamespace(_command=Mock())
    with pytest.raises(ProcessError):
        proof.launch(cli, proof.names(TOKEN)[0], IMAGE_ID, TOKEN)
    cli._command.assert_not_called()


def test_uncertain_launch_not_blindly_retried(proof, monkeypatch):
    monkeypatch.setattr(proof, "owned", lambda *args: None)
    cli = SimpleNamespace(
        _command=Mock(side_effect=ProcessError("research_process_uncertain"))
    )
    with pytest.raises(ProcessError):
        proof.launch(cli, proof.names(TOKEN)[0], IMAGE_ID, TOKEN)
    assert cli._command.call_count == 1


def test_empty_and_complete_reports_validate(proof):
    assert proof.validate_report(proof.empty_report()) == proof.empty_report()
    assert proof.validate_report(complete_report(proof))["complete"] is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", True),
        ("schema", 2),
        ("scope", SECRET),
        ("production_acceptance", True),
        ("strict_bounds_disclosed", False),
        ("peer_authentication_exercised", True),
        ("complete", 1),
        ("cleanup", None),
        ("api_compatible", "yes"),
        ("authority_equal", 1),
    ],
)
def test_report_fixed_scope_and_exact_scalar_types(proof, field, value):
    report = complete_report(proof)
    report[field] = value
    with pytest.raises(ProcessError):
        proof.validate_report(report)


@pytest.mark.parametrize(
    "mutation",
    [
        "extra_root",
        "missing_root",
        "samples_list",
        "extra_mode",
        "four_samples",
        "extra_sample",
        "sample_list",
        "missing_count",
        "extra_count",
        "count_bool",
        "count_negative",
        "count_excess",
        "private_error",
        "error_bool",
        "nan",
        "infinity",
        "negative_seconds",
        "excess_seconds",
        "bool_seconds",
        "negatives_list",
        "extra_negative",
        "extra_negative_mode",
        "negative_numeric",
    ],
)
def test_report_closed_nested_fields_and_bounds(proof, mutation):
    report = complete_report(proof)
    sample = report["samples"]["api"][0]
    if mutation == "extra_root":
        report["private"] = SECRET
    elif mutation == "missing_root":
        del report["scope"]
    elif mutation == "samples_list":
        report["samples"] = []
    elif mutation == "extra_mode":
        report["samples"]["other"] = []
    elif mutation == "four_samples":
        report["samples"]["api"].append(copy.deepcopy(sample))
    elif mutation == "extra_sample":
        sample["private"] = SECRET
    elif mutation == "sample_list":
        report["samples"]["api"][0] = []
    elif mutation == "missing_count":
        del sample["counts"]["host"]
    elif mutation == "extra_count":
        sample["counts"]["private"] = 0
    elif mutation.startswith("count_"):
        sample["counts"]["top"] = {
            "count_bool": True,
            "count_negative": -1,
            "count_excess": 4,
        }[mutation]
    elif mutation == "private_error":
        sample["error"] = SECRET
    elif mutation == "error_bool":
        sample["error"] = True
    elif mutation in (
        "nan",
        "infinity",
        "negative_seconds",
        "excess_seconds",
        "bool_seconds",
    ):
        sample["seconds"] = {
            "nan": float("nan"),
            "infinity": float("inf"),
            "negative_seconds": -1,
            "excess_seconds": 30.001,
            "bool_seconds": True,
        }[mutation]
    elif mutation == "negatives_list":
        report["negatives"] = []
    elif mutation == "extra_negative":
        report["negatives"][SECRET] = {"cli": True, "api": True}
    elif mutation == "extra_negative_mode":
        report["negatives"]["paused"][SECRET] = True
    elif mutation == "negative_numeric":
        report["negatives"]["paused"]["cli"] = 1
    with pytest.raises(ProcessError):
        proof.validate_report(report)


@pytest.mark.parametrize(
    "mutation",
    [
        "no_samples",
        "partial_counts",
        "sample_error",
        "negative_false",
        "negative_unknown",
        "cleanup_false",
        "api_false",
        "equal_false",
    ],
)
def test_completion_requires_all_success_evidence(proof, mutation):
    report = complete_report(proof)
    if mutation == "no_samples":
        report["samples"]["api"] = []
    elif mutation == "partial_counts":
        report["samples"]["api"][0]["counts"]["inspect"] = 5
    elif mutation == "sample_error":
        report["samples"]["api"][0]["error"] = "oe_controller_daemon_identity"
    elif mutation == "negative_false":
        report["negatives"]["missing"]["api"] = False
    elif mutation == "negative_unknown":
        report["negatives"]["missing"]["api"] = None
    else:
        report[
            {
                "cleanup_false": "cleanup",
                "api_false": "api_compatible",
                "equal_false": "authority_equal",
            }[mutation]
        ] = False
    with pytest.raises(ProcessError):
        proof.validate_report(report)
    report["complete"] = False
    proof.validate_report(report)


@pytest.mark.parametrize(
    "code",
    [
        "oe_controller_daemon_identity",
        "research_process_uncertain",
        "research_process_output",
        "research_process_identity",
    ],
)
def test_only_coarse_failure_codes_can_be_published(proof, code):
    report = proof.empty_report()
    report["samples"]["api"].append(
        {"counts": {"inspect": 1, "top": 0, "host": 0}, "seconds": 0.01, "error": code}
    )
    proof.validate_report(report)
    assert SECRET not in json.dumps(report)


def test_publish_one_complete_closed_line(proof, tmp_path, capsys):
    path = tmp_path / "proof.log"
    report = complete_report(proof)
    path.write_bytes(encoded(report))
    assert proof.publish(path) == 0
    raw = capsys.readouterr().out
    assert raw.count("\n") == 1 and not raw.startswith(PREFIX.decode())
    assert json.loads(raw) == report


@pytest.mark.parametrize(
    "mutation",
    [
        "empty",
        "wrong_prefix",
        "second_line",
        "missing_newline",
        "private_extra_field",
        "duplicate_root",
        "duplicate_nested",
        "malformed",
        "oversize",
        "nan",
    ],
)
def test_publish_malformed_private_or_unbounded_input_emits_nothing(
    proof, tmp_path, capsys, mutation
):
    raw = encoded(complete_report(proof))
    if mutation == "empty":
        raw = b""
    elif mutation == "wrong_prefix":
        raw = b"wrong:" + raw
    elif mutation == "second_line":
        raw += SECRET.encode() + b"\n"
    elif mutation == "missing_newline":
        raw = raw[:-1]
    elif mutation == "private_extra_field":
        raw = raw.replace(
            b'{"schema":1,', b'{"schema":1,"private":"' + SECRET.encode() + b'",', 1
        )
    elif mutation == "duplicate_root":
        raw = raw.replace(b'{"schema":1,', b'{"schema":1,"schema":1,', 1)
    elif mutation == "duplicate_nested":
        raw = raw.replace(b'"inspect":6,', b'"inspect":6,"inspect":6,', 1)
    elif mutation == "malformed":
        raw = PREFIX + b'{"private":"' + SECRET.encode() + b'"\n'
    elif mutation == "oversize":
        raw = PREFIX + b" " * 32768 + b"{}\n"
    elif mutation == "nan":
        raw = raw.replace(b'"seconds":0.25', b'"seconds":NaN', 1)
    path = tmp_path / "proof.log"
    path.write_bytes(raw)
    with pytest.raises((ProcessError, ValueError)):
        proof.publish(path)
    assert capsys.readouterr().out == ""


def test_publish_exact_32768_byte_boundary(proof, tmp_path, capsys):
    raw = encoded(proof.empty_report())
    padded = raw[:-1] + b" " * (32768 - len(raw)) + b"\n"
    assert len(padded) == 32768
    path = tmp_path / "proof.log"
    path.write_bytes(padded)
    assert proof.publish(path) == 1
    assert len(capsys.readouterr().out) < 32768
    path.write_bytes(padded[:-1] + b" \n")
    with pytest.raises(ProcessError):
        proof.publish(path)


@pytest.mark.parametrize(
    "error", [RuntimeError(SECRET), ProcessError(SECRET), ValueError(SECRET)]
)
def test_main_failure_does_not_publish_raw_exception(proof, monkeypatch, capsys, error):
    monkeypatch.setattr(proof, "run", Mock(side_effect=error))
    monkeypatch.setattr(proof.sys, "argv", ["oe_docker_transport_proof.py"])
    assert proof.main() == 1
    output = capsys.readouterr().out
    assert SECRET not in output
    assert json.loads(output[len(PREFIX) :]) == proof.empty_report()


def test_claim_requires_reserved_name_and_private_single_use_marker(proof):
    name = proof.names(TOKEN)[0]
    assert proof.claimed(name, TOKEN) is False
    proof.claim(name, TOKEN)
    path = proof.claim_path(name, TOKEN)
    assert path.read_bytes() == b"owned-inert-fixture-v1\n"
    assert path.stat().st_mode & 0o777 == 0o600
    assert proof.claimed(name, TOKEN) is True
    with pytest.raises(FileExistsError):
        proof.claim(name, TOKEN)
    with pytest.raises(ProcessError):
        proof.claim_path("arbitrary-name", TOKEN)


@pytest.mark.parametrize(
    "damage", ["content", "permissions", "hardlink", "symlink", "directory"]
)
def test_claim_tampering_never_authorizes_cli(proof, tmp_path, damage):
    name = proof.names(TOKEN)[0]
    proof.claim(name, TOKEN)
    path = proof.claim_path(name, TOKEN)
    if damage == "content":
        path.write_bytes(b"foreign")
    elif damage == "permissions":
        path.chmod(0o644)
    elif damage == "hardlink":
        (tmp_path / "alias").hardlink_to(path)
    elif damage == "symlink":
        other = tmp_path / "other"
        other.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(other)
    elif damage == "directory":
        path.unlink()
        path.mkdir()
    cli = SimpleNamespace(_command=Mock())
    with pytest.raises((ProcessError, OSError)):
        proof.cleanup(cli, name, IMAGE_ID, TOKEN)
    cli._command.assert_not_called()


def test_unclaimed_collision_is_not_even_inspected_or_removed(proof):
    cli = SimpleNamespace(_command=Mock())
    proof.cleanup(cli, proof.names(TOKEN)[0], IMAGE_ID, TOKEN)
    cli._command.assert_not_called()


def test_cleanup_removes_full_cid_and_checks_both_absence_targets(proof):
    name = proof.names(TOKEN)[0]
    proof.claim(name, TOKEN)
    calls = []
    removed = False

    def command(args, **kwargs):
        nonlocal removed
        calls.append(args)
        if args[0] == "rm":
            assert args == ["rm", "--force", CID]
            removed = True
            return 0, (CID + "\n").encode()
        if removed:
            return 1, b"No such container: fixed"
        return 0, f"{CID}|{IMAGE_ID}|{TOKEN}\n".encode()

    proof.cleanup(SimpleNamespace(_command=command), name, IMAGE_ID, TOKEN)
    assert [args[-1] for args in calls if args[0] == "container"] == [name, CID, name]
    assert not proof.claim_path(name, TOKEN).exists()


@pytest.mark.parametrize("survives", ["cid", "name"])
def test_cleanup_does_not_claim_success_if_container_survives(proof, survives):
    name = proof.names(TOKEN)[0]
    proof.claim(name, TOKEN)
    removed = False

    def command(args, **kwargs):
        nonlocal removed
        if args[0] == "rm":
            removed = True
            return 0, b""
        if not removed or args[-1] == (CID if survives == "cid" else name):
            return 0, f"{CID}|{IMAGE_ID}|{TOKEN}\n".encode()
        return 1, b"No such container: fixed"

    with pytest.raises(ProcessError):
        proof.cleanup(SimpleNamespace(_command=command), name, IMAGE_ID, TOKEN)
    assert proof.claim_path(name, TOKEN).exists()


class FakeDaemon:
    """Fixed lifecycle response model. It never delegates to a real command."""

    def __init__(self, proof):
        self.proof = proof
        self.containers = {}
        self.commands = []
        self.sequence_calls = []
        self.inspect_calls = []
        self.next_id = 0
        self.max_running = 0
        self.oversize_errors = {
            "cli": "research_process_output",
            "api": "oe_controller_daemon_identity",
        }
        self.positive_damage = None
        self.negative_accept = None

    def find(self, target):
        return next(
            (
                item
                for item in self.containers.values()
                if item["name"] == target or item["cid"] == target
            ),
            None,
        )

    def _command(self, args, **kwargs):
        self.commands.append(copy.deepcopy(args))
        if args[:2] == ["image", "inspect"]:
            return 0, (IMAGE_ID + "\n").encode()
        if args[:2] == ["container", "inspect"]:
            item = self.find(args[-1])
            if item is None:
                return 1, b"No such container: fixed"
            return 0, f"{item['cid']}|{item['image']}|{item['owner']}\n".encode()
        if args[0] == "run":
            assert not self.containers, "The two fixtures must never overlap"
            self.next_id += 1
            cid = f"{self.next_id:064x}"
            name = args[args.index("--name") + 1]
            self.containers[cid] = {
                "cid": cid,
                "name": name,
                "image": IMAGE_ID,
                "owner": TOKEN,
                "state": "running",
                "oversized": name.endswith("-oversize"),
            }
            self.max_running = max(self.max_running, len(self.containers))
            return 0, (cid + "\n").encode()
        if args[0] == "rm":
            assert args[:2] == ["rm", "--force"]
            assert len(args[-1]) == 64
            del self.containers[args[-1]]
            return 0, b""
        if args[0] in ("pause", "unpause", "stop"):
            item = self.find(args[-1])
            assert item is not None
            item["state"] = {
                "pause": "paused",
                "unpause": "running",
                "stop": "stopped",
            }[args[0]]
            return 0, b""
        raise AssertionError(f"Unexpected mocked lifecycle operation: {args[0]}")

    def inspect(self, mode, cid):
        self.inspect_calls.append((mode, cid))
        item = self.find(cid)
        assert item is not None and item["oversized"]
        code = self.oversize_errors[mode]
        if code is not None:
            raise ProcessError(code)
        return {"Id": cid, "Image": IMAGE_ID}

    def _inspect(self, cid):
        return self.inspect("cli", cid)

    def sequence(self, mode, cid, image, pids):
        item = self.find(cid)
        state = "missing" if item is None else item["state"]
        assert item is None or not item["oversized"], (
            "Oversize must call inspect directly, never stale PID validation"
        )
        self.sequence_calls.append((mode, state, cid))
        sample = {
            "counts": {"inspect": 6, "top": 3, "host": 12},
            "seconds": 0.1,
            "error": None,
        }
        if state == "running":
            result = ("opaque-identity",)
            if self.positive_damage == "counts":
                sample["counts"]["host"] = 11
            elif self.positive_damage == "error":
                sample["error"] = "oe_controller_daemon_identity"
            elif self.positive_damage == "identity" and mode == "api":
                result = ("drift",)
            return result, sample
        sample["counts"] = {"inspect": 1, "top": 0, "host": 0}
        if self.negative_accept != state:
            sample["error"] = "oe_controller_daemon_identity"
        return None, sample


@pytest.fixture
def flow(proof, monkeypatch):
    daemon = FakeDaemon(proof)
    monkeypatch.setattr(proof, "DockerSupervisor", lambda image: daemon)
    monkeypatch.setattr(
        proof,
        "DockerReadTransport",
        lambda image, deadline: SimpleNamespace(
            _inspect=lambda cid: daemon.inspect("api", cid)
        ),
    )
    monkeypatch.setattr(proof, "context", lambda *args: (100, 101, 102))
    monkeypatch.setattr(proof, "sequence", daemon.sequence)
    return daemon


def test_complete_mocked_flow_two_fixtures_sequential_and_all_negatives(proof, flow):
    report = proof.run()
    assert report["complete"] is True and report["cleanup"] is True
    proof.validate_report(report)
    assert report["peer_authentication_exercised"] is False
    assert report["production_acceptance"] is False
    assert flow.max_running == 1 and flow.containers == {}
    assert [mode for mode, state, cid in flow.sequence_calls if state == "running"] == [
        "cli",
        "api",
    ] * 3
    assert [state for mode, state, cid in flow.sequence_calls[6:]] == ["paused"] * 2 + [
        "stopped"
    ] * 2 + ["missing"] * 2
    assert flow.inspect_calls == [("cli", f"{2:064x}"), ("api", f"{2:064x}")]
    lifecycle = [
        args[0]
        for args in flow.commands
        if args[0] != "container" and args[0] != "image"
    ]
    assert lifecycle == ["run", "pause", "unpause", "stop", "rm", "run", "rm"]


@pytest.mark.parametrize("damage", ["counts", "error", "identity"])
def test_positive_mismatch_never_completes_but_cleans(proof, flow, damage):
    flow.positive_damage = damage
    report = proof.run()
    assert report["complete"] is False and report["cleanup"] is True
    assert flow.containers == {}
    proof.validate_report(report)


@pytest.mark.parametrize("state", ["paused", "stopped", "missing"])
def test_any_negative_acceptance_prevents_complete(proof, flow, state):
    flow.negative_accept = state
    report = proof.run()
    assert report["complete"] is False and report["cleanup"] is True
    assert report["negatives"][state] == {"cli": False, "api": False}
    assert flow.containers == {}


@pytest.mark.parametrize(
    "mode,code",
    [
        ("cli", None),
        ("api", None),
        ("cli", "oe_controller_daemon_identity"),
        ("api", "research_process_output"),
        ("cli", "research_process_uncertain"),
        ("api", SECRET),
    ],
)
def test_oversize_requires_specific_transport_rejection_not_any_error(
    proof, flow, mode, code
):
    flow.oversize_errors[mode] = code
    report = proof.run()
    assert report["complete"] is False
    assert report["negatives"]["oversize"][mode] is False
    assert SECRET not in json.dumps(report)
    assert report["cleanup"] is True and flow.containers == {}


def test_initial_preexisting_same_owner_collision_is_preserved(proof, flow):
    flow.containers[CID] = {
        "cid": CID,
        "name": proof.names(TOKEN)[0],
        "image": IMAGE_ID,
        "owner": TOKEN,
        "state": "running",
        "oversized": False,
    }
    report = proof.run()
    assert report["complete"] is False
    assert CID in flow.containers
    assert not any(args[0] in {"run", "rm", "pause", "stop"} for args in flow.commands)
    proof.cleanup(flow, proof.names(TOKEN)[0], IMAGE_ID, TOKEN)
    assert CID in flow.containers


@pytest.fixture
def sequence_case(proof, monkeypatch):
    events = []
    deadline_seen = []
    now = [10.0]
    document = {
        "Id": CID,
        "Image": IMAGE_ID,
        "State": {"Running": True, "Status": "running", "Pid": 100},
    }
    hosts = {
        pid: (
            pid,
            pid + 1000,
            "0::/unit",
            1 if pid == 100 else 100,
            (65533,) * 4,
            (65533,) * 4,
        )
        for pid in (100, 101, 102)
    }
    raw = b"PID UID PPID PIDNS COMMAND\n100 65533 1 42 python\n101 65533 100 42 python\n102 65533 100 42 python\n"
    hooks = SimpleNamespace(inspect=lambda: None, host=lambda pid: None)

    def inspect(cid):
        assert cid == CID
        events.append("inspect")
        hooks.inspect()
        return copy.deepcopy(document)

    def command(args):
        assert args == ["top", CID, "-eo", proof.observer.TOP_FIELDS]
        events.append("top")
        return 0, raw

    def host(pid):
        events.append(f"host{pid}")
        hooks.host(pid)
        return hosts[pid]

    client = SimpleNamespace(_inspect=inspect, _command=command)

    def api(image, *, deadline):
        assert image == IMAGE_ID
        deadline_seen.append(deadline)
        return client

    monkeypatch.setattr(proof, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(
        proof.observer, "time", SimpleNamespace(monotonic=lambda: now[0])
    )
    monkeypatch.setattr(proof.observer, "_host_snapshot", host)
    monkeypatch.setattr(proof, "DockerSupervisor", lambda image: client)
    monkeypatch.setattr(proof, "DockerReadTransport", api)
    return SimpleNamespace(
        events=events,
        deadline_seen=deadline_seen,
        now=now,
        hooks=hooks,
        client=client,
        host=host,
    )


@pytest.mark.parametrize("mode", ["cli", "api"])
def test_actual_sequence_wrapper_retains_original_observer_order_and_counts(
    proof, sequence_case, mode
):
    result, sample = proof.sequence(mode, CID, IMAGE_ID, (100, 101, 102))
    assert sample == {
        "counts": {"inspect": 6, "top": 3, "host": 12},
        "seconds": 0.0,
        "error": None,
    }
    assert result[0] == result[2]
    assert result[1].host_pid == 102
    assert sequence_case.events == [
        "inspect",
        "host100",
        "host101",
        "top",
        "host100",
        "host101",
        "inspect",
        "inspect",
        "host100",
        "host102",
        "top",
        "host100",
        "host102",
        "inspect",
        "inspect",
        "host100",
        "host101",
        "top",
        "host100",
        "host101",
        "inspect",
    ]
    assert sequence_case.deadline_seen == ([18.0] if mode == "api" else [])
    assert proof.observer._host_snapshot is sequence_case.host


@pytest.mark.parametrize("mode", ["cli", "api"])
def test_sequence_original_eight_second_deadline_is_not_reset(
    proof, sequence_case, mode
):
    sequence_case.hooks.host = lambda pid: sequence_case.now.__setitem__(0, 18.0)
    result, sample = proof.sequence(mode, CID, IMAGE_ID, (100, 101, 102))
    assert result is None and sample["error"] == "oe_controller_daemon_identity"
    assert sample["counts"] == {"inspect": 1, "top": 1, "host": 2}
    assert sample["seconds"] == 8.0
    assert sequence_case.deadline_seen == ([18.0] if mode == "api" else [])
    assert proof.observer._host_snapshot is sequence_case.host


@pytest.mark.parametrize(
    "code",
    [
        "oe_controller_daemon_identity",
        "research_process_uncertain",
        "research_process_output",
        "research_process_identity",
        SECRET,
    ],
)
def test_sequence_reports_only_allowed_error_codes(proof, sequence_case, code):
    sequence_case.hooks.inspect = Mock(side_effect=ProcessError(code))
    if code == SECRET:
        with pytest.raises(ProcessError, match="^oe_controller_daemon_identity$"):
            proof.sequence("api", CID, IMAGE_ID, (100, 101, 102))
    else:
        result, sample = proof.sequence("api", CID, IMAGE_ID, (100, 101, 102))
        assert result is None and sample["error"] == code
        assert sample["counts"] == {"inspect": 1, "top": 0, "host": 0}
    assert proof.observer._host_snapshot is sequence_case.host


@pytest.mark.parametrize("elapsed", [float("nan"), float("inf"), -0.01, 30.01])
def test_sequence_rejects_invalid_elapsed_reporting(
    proof, sequence_case, monkeypatch, elapsed
):
    monkeypatch.setattr(
        proof,
        "time",
        SimpleNamespace(monotonic=Mock(side_effect=[10.0, 11.0, 11.0 + elapsed])),
    )
    with pytest.raises(ProcessError):
        proof.sequence("api", CID, IMAGE_ID, (100, 101, 102))


def test_uncertain_create_is_cleaned_by_exact_verified_identity_without_retry(
    proof, flow, monkeypatch
):
    original = flow._command

    def command(args, **kwargs):
        result = original(args, **kwargs)
        if args[0] == "run":
            raise ProcessError("research_process_uncertain")
        return result

    monkeypatch.setattr(flow, "_command", command)
    report = proof.run()
    assert report["complete"] is False and report["cleanup"] is True
    assert flow.containers == {}
    assert sum(args[0] == "run" for args in flow.commands) == 1
    assert [args for args in flow.commands if args[0] == "rm"] == [
        ["rm", "--force", f"{1:064x}"]
    ]


def test_final_cleanup_checks_both_names_after_first_cleanup_failure(
    proof, flow, monkeypatch
):
    seen = []
    original = proof.cleanup

    def cleanup(cli, name, image, token):
        seen.append(name)
        if name.endswith("-normal"):
            raise ProcessError(SECRET)
        return original(cli, name, image, token)

    monkeypatch.setattr(proof, "cleanup", cleanup)
    report = proof.run()
    assert report["complete"] is False and report["cleanup"] is False
    assert seen[-2:] == proof.names(TOKEN)
    assert SECRET not in json.dumps(report)


def test_independent_cleanup_attempts_both_claimed_names_and_sanitizes_errors(
    proof, monkeypatch, capsys
):
    seen = []
    monkeypatch.setattr(proof, "DockerSupervisor", lambda image: object())
    monkeypatch.setattr(proof, "image_id", lambda cli: IMAGE_ID)

    def cleanup(cli, name, image, token):
        seen.append(name)
        if name.endswith("-normal"):
            raise ProcessError(SECRET)

    monkeypatch.setattr(proof, "cleanup", cleanup)
    monkeypatch.setattr(
        proof.sys, "argv", ["oe_docker_transport_proof.py", "--cleanup"]
    )
    assert proof.main() == 1
    assert seen == proof.names(TOKEN)
    assert SECRET not in capsys.readouterr().out


@pytest.fixture
def workflow():
    import re

    path = (
        Path(__file__).parents[2]
        / ".github"
        / "workflows"
        / "mineagent-structured-factor.yml"
    )
    source = path.read_text()
    assert 0 < len(source) < 16384
    # Source-wiring assertions for this deliberately fixed small workflow, not a
    # general YAML parser. No optional PyYAML dependency is introduced.
    steps = []
    for block in re.split(r"(?m)^      - ", source)[1:]:
        lines = block.splitlines()
        key, value = lines[0].split(":", 1)
        step = {key: value.strip()}
        index = 1
        while index < len(lines):
            line = lines[index]
            if not line:
                index += 1
                continue
            assert line.startswith("        ") and not line.startswith("         ")
            key, value = line[8:].split(":", 1)
            value = value.strip()
            index += 1
            if key == "run" and value == "|":
                body = []
                while index < len(lines) and lines[index].startswith("          "):
                    body.append(lines[index][10:])
                    index += 1
                step[key] = "\n".join(body)
            elif key == "with":
                options = {}
                while index < len(lines) and lines[index].startswith("          "):
                    name, option = lines[index][10:].split(":", 1)
                    option = option.strip()
                    options[name] = (
                        False
                        if option == "false"
                        else int(option)
                        if option.isdigit()
                        else option
                    )
                    index += 1
                step[key] = options
            else:
                step[key] = value
        steps.append(step)
    return source, steps


def test_workflow_is_dispatch_only_readonly_and_single_bounded_job(workflow):
    import re

    source, steps = workflow
    assert (
        "\non:\n  workflow_dispatch:\n\npermissions:\n  contents: read\n\njobs:\n"
        in source
    )
    assert re.findall(r"(?m)^  ([a-z0-9-]+):\n    runs-on:", source) == [
        "inert-transport-proof"
    ]
    assert "    runs-on: ubuntu-22.04\n    timeout-minutes: 15\n    steps:" in source
    assert "    services:" not in source and "    env:" not in source
    assert steps[0]["with"]["persist-credentials"] is False
    for step in steps:
        if "uses" in step:
            assert (
                re.fullmatch(r"[a-f0-9]{40}", step["uses"].rsplit("@", 1)[1])
                is not None
            )


def test_workflow_installs_only_existing_hashed_host_lock(workflow):
    _, steps = workflow
    installs = [
        step["run"] for step in steps if "run" in step and "pip install" in step["run"]
    ]
    assert installs == [
        "python -m pip install --require-hashes -r backend/requirements.lock"
    ]
    combined = "\n".join(step.get("run", "") for step in steps)
    assert not any(
        value in combined
        for value in (
            "docker build",
            "docker compose",
            "npm ",
            "npx ",
            "alembic",
            "provider-sdk",
            "pip download",
            "sudo",
        )
    )


def test_workflow_pulls_only_exact_digest_with_no_inherited_configuration(
    proof, workflow
):
    _, steps = workflow
    pulls = [step["run"] for step in steps if "run" in step and " pull " in step["run"]]
    assert len(pulls) == 1
    source = pulls[0]
    assert proof.IMAGE in source
    assert (
        'env -i PATH="$PATH" HOME=/nonexistent DOCKER_HOST=unix:///var/run/docker.sock'
        in source
    )
    assert "docker --config /nonexistent pull" in source
    assert "> var/numeric-private/image-pull.log 2>&1" in source
    assert "chmod 700 var/numeric-private" in source


def test_workflow_raw_output_private_and_only_validated_artifact_uploaded(workflow):
    _, steps = workflow
    compare = next(
        step for step in steps if "run" in step and "--publish" in step["run"]
    )
    source = compare["run"]
    assert (
        "> var/numeric-private/transport.stdout 2> var/numeric-private/transport.stderr"
        in source
    )
    assert "--publish var/numeric-private/transport.stdout" in source
    assert (
        "> var/numeric-proof/transport-proof.json 2> var/numeric-private/publish.stderr"
        in source
    )
    assert "cat var/numeric-proof/transport-proof.json" in source
    assert "cat var/numeric-private" not in source and "tee " not in source
    uploads = [
        step
        for step in steps
        if step.get("uses", "").startswith("actions/upload-artifact@")
    ]
    assert len(uploads) == 1
    assert uploads[0]["with"]["path"] == "var/numeric-proof/transport-proof.json"
    assert uploads[0]["if"] == "always()"
    assert uploads[0]["with"]["retention-days"] == 7


def test_workflow_independent_cleanup_always_runs_before_artifact_upload(workflow):
    _, steps = workflow
    indexes = [
        index for index, step in enumerate(steps) if "--cleanup" in step.get("run", "")
    ]
    assert len(indexes) == 1
    index = indexes[0]
    cleanup = steps[index]
    assert cleanup["if"] == "always()"
    assert "python -m script.oe_docker_transport_proof --cleanup" in cleanup["run"]
    assert (
        "> var/numeric-private/cleanup.stdout 2> var/numeric-private/cleanup.stderr"
        in cleanup["run"]
    )
    assert index < next(
        index
        for index, step in enumerate(steps)
        if step.get("uses", "").startswith("actions/upload-artifact@")
    )


def test_workflow_failed_proof_still_publishes_closed_failure_under_github_errexit(
    workflow, tmp_path
):
    import os
    import subprocess

    _, steps = workflow
    source = next(step["run"] for step in steps if "--publish" in step.get("run", ""))
    root = tmp_path / "shell-proof"
    root.mkdir()
    (root / "var/numeric-private").mkdir(parents=True)
    (root / "var/numeric-proof").mkdir(parents=True)
    binaries = root / "bin"
    binaries.mkdir()
    fake = binaries / "python"
    fake.write_text(
        '#!/bin/sh\ncase "$*" in\n'
        '*--publish*) printf \'{"complete":false,"cleanup":true}\\n\'; printf "publish\\n" >> "$TRACE"; exit 1;;\n'
        '*) printf "synthetic-private-daemon-data\\n"; printf "proof\\n" >> "$TRACE"; exit 1;;\nesac\n'
    )
    fake.chmod(0o700)
    trace = root / "trace"
    completed = subprocess.run(
        ["/bin/bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", source],
        cwd=root,
        env={"PATH": str(binaries) + ":" + os.defpath, "TRACE": str(trace)},
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert completed.returncode != 0
    assert trace.read_text().splitlines() == ["proof", "publish"]
    assert SECRET.encode() not in completed.stdout + completed.stderr
    assert json.loads(completed.stdout) == {"complete": False, "cleanup": True}


@pytest.mark.parametrize(
    "raw", [SECRET.encode(), PREFIX + b'{"schema":1,"schema":1}\n', b"x" * 32769]
)
def test_main_failed_publish_still_emits_only_plain_closed_json(
    proof, tmp_path, monkeypatch, capsys, raw
):
    path = tmp_path / "private-input.log"
    path.write_bytes(raw)
    monkeypatch.setattr(
        proof.sys, "argv", ["oe_docker_transport_proof.py", "--publish", str(path)]
    )
    assert proof.main() == 1
    output = capsys.readouterr().out
    assert SECRET not in output and not output.startswith(PREFIX.decode())
    assert json.loads(output) == proof.empty_report()
