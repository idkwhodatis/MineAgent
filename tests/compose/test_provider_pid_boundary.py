"""Local boundary regressions, not actual Docker acceptance."""

import copy
import hashlib
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import yaml

from script import ci_rd_compose_identity as proof

ROOT = Path(__file__).resolve().parents[2]


def inspected(*, host=False, candidate=False):
    return {
        "Id": "a" * 64,
        "Config": {"User": "1000:1000"},
        "HostConfig": {
            "PidMode": "host" if host else "",
            "Privileged": False,
            "ReadonlyRootfs": True,
            "CapAdd": ["SETUID", "SETGID", "KILL"] if candidate else None,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "NetworkMode": "none" if candidate else "project_application",
        },
        "Mounts": [],
    }


def test_only_two_trusted_services_opt_in_and_base_unchanged():
    overlay = yaml.safe_load((ROOT / "infra/compose.providers.yml").read_text())[
        "services"
    ]
    assert {n for n, v in overlay.items() if v.get("pid") == "host"} == {
        "research-worker",
        "evaluator-reconciler",
    }
    for name, v in overlay.items():
        assert set(v) == (
            {"env_file", "pid"}
            if name in ("research-worker", "evaluator-reconciler")
            else {"env_file"}
        )
    base = yaml.safe_load((ROOT / "infra/compose.full.yml").read_text())["services"]
    assert all("pid" not in v for v in base.values())


@pytest.mark.parametrize(
    "host,candidate", [(True, False), (False, False), (False, True)]
)
def test_actual_inspect_projection_accepts_only_expected_boundary(host, candidate):
    proof.require_boundary(
        inspected(host=host, candidate=candidate), host_pid=host, candidate=candidate
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("PidMode", "host"),
        ("NetworkMode", "host"),
        ("NetworkMode", "application"),
        ("Privileged", True),
        ("ReadonlyRootfs", False),
        ("CapAdd", ["SYS_PTRACE"]),
        ("CapDrop", []),
        ("SecurityOpt", []),
    ],
)
def test_candidate_isolation_regressions_rejected(field, value):
    record = inspected(candidate=True)
    record["HostConfig"][field] = value
    with pytest.raises(ValueError):
        proof.require_boundary(record, host_pid=False, candidate=True)


@pytest.mark.parametrize("path", ["/", "/proc"])
def test_host_root_mounts_rejected(path):
    record = inspected(host=True)
    record["Mounts"] = [{"Source": path, "Destination": "/unexpected"}]
    with pytest.raises(ValueError):
        proof.require_boundary(record, host_pid=True)


def test_private_app_cannot_receive_daemon_socket_or_root_user():
    for key in ("mount", "user"):
        record = inspected()
        if key == "mount":
            record["Mounts"] = [
                {
                    "Source": "/var/run/docker.sock",
                    "Destination": "/var/run/docker.sock",
                }
            ]
        else:
            record["Config"]["User"] = "0:0"
        with pytest.raises(ValueError):
            proof.require_boundary(record, host_pid=False)


def test_receipt_bytes_and_digest_are_both_exact():
    payload = {"peer_pid": 123, "peer_uid": 65533}
    record = NS(model_dump=lambda **_: payload)
    row = NS(
        payload=copy.deepcopy(payload),
        payload_sha256=hashlib.sha256(proof.canonical(payload)).hexdigest(),
    )
    proof.receipt_matches(record, row)
    row.payload["peer_pid"] = 124
    with pytest.raises(ValueError):
        proof.receipt_matches(record, row)
    row.payload = payload
    row.payload_sha256 = "f" * 64
    with pytest.raises(ValueError):
        proof.receipt_matches(record, row)


def test_actual_gate_requires_live_observer_before_positive_cleanup_publication():
    script = (ROOT / "script/ci_rd_provider_compose.sh").read_text()
    assert (
        'ci_rd_compose_identity applications "$api" "$dispatcher" "$worker" "$reconciler"'
        in script
    )
    assert "ci_rd_compose_identity probe" in script
    assert script.index("ci_rd_compose_identity probe") < script.index(
        "ci_rd_compose_inside.py run"
    )
    assert "identity-observed.json" in script
    source = (ROOT / "script/ci_rd_compose_identity.py").read_text()
    assert "context.observe_pair(child.peer_pid)" in source
    assert "context.observe(os.getpid())" in source
    assert "original_deadline(launch.intent.not_after)" in source
    assert "root, admission, with_child=True" in source
    assert (
        "client, identity.container_id, identity.image_id, identity.init_pid" in source
    )
    assert "subprocess" not in source and "print(" not in source


def test_controller_requires_actual_peer_and_rejects_unrelated_pid(monkeypatch):
    from datetime import UTC, datetime, timedelta

    from runtimes.rdagent_operational import daemon_identity, process_journal

    leader = NS(host_pid=101)
    child_kernel = NS(host_pid=102)
    child = NS(peer_pid=102, peer_uid=65533, peer_gid=65533, controller=child_kernel)
    identity = NS(container_id="a" * 64, image_id="sha256:" + "b" * 64, leader=leader)
    launch = NS(intent=NS(not_after=datetime.now(UTC) + timedelta(seconds=30)))
    monkeypatch.setattr(
        process_journal,
        "read_controller_journal",
        lambda *a, **k: (launch, identity, child, None),
    )
    matched = []
    monkeypatch.setattr(
        proof, "receipt_matches", lambda obj, row: matched.append((obj, row))
    )

    class Context:
        def __init__(self, *args):
            assert args[:3] == (identity.container_id, identity.image_id, 101)

        def observe_pair(self, pid):
            assert pid == 102
            return leader, child_kernel

        def observe(self, pid):
            assert pid == proof.os.getpid()
            raise proof.ProcessError("rd_controller_daemon_identity")

    monkeypatch.setattr(daemon_identity, "DaemonIdentityContext", Context)
    assert proof.controller(
        "/retained", object(), {"launch_identity": 1, "controller_child": 2}
    )
    assert matched == [(identity, 1), (child, 2)]
    monkeypatch.setattr(Context, "observe", lambda self, pid: child_kernel)
    with pytest.raises(ValueError, match="Unrelated"):
        proof.controller(
            "/retained", object(), {"launch_identity": 1, "controller_child": 2}
        )
    child.peer_uid = 0
    with pytest.raises(ValueError, match="kernel peer"):
        proof.controller(
            "/retained", object(), {"launch_identity": 1, "controller_child": 2}
        )


def test_synthetic_delay_is_exact_source_code_not_runtime_policy_change(monkeypatch):
    import importlib.util
    import sys

    from packages.rdagent_adapter.response_parser import parse_selected_responses
    from packages.research_runtime.engine_broker_contracts import RDChatResponse
    from script.ci_rd_compose_inside import compose_source

    spec = importlib.util.spec_from_file_location(
        "rd_provider_fixture", ROOT / "tests/integration/rd_provider_fixture.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    monkeypatch.setitem(sys.modules, "rd_provider_fixture", fixture)
    source = compose_source()
    assert source.replace("import time\ntime.sleep(3)\n", "") == fixture.SOURCE
    import json

    response = RDChatResponse.model_validate(
        {
            "content": json.dumps({"code": source}),
            "finish_reason": "stop",
        }
    )
    assert parse_selected_responses((response,)).source == source


@pytest.mark.parametrize(
    "change", [None, "Uid", "Gid", "CapEff", "CapPrm", "CapInh", "CapAmb", "NoNewPrivs"]
)
def test_generated_child_drop_not_confused_with_protected_pid1_caps(change):
    values = {
        "Uid": "65533 65533 65533 65533",
        "Gid": "65533 65533 65533 65533",
        "CapEff": "0",
        "CapPrm": "0",
        "CapInh": "0",
        "CapAmb": "0",
        "NoNewPrivs": "1",
    }
    if change:
        values[change] = (
            "0 0 0 0"
            if change in ("Uid", "Gid")
            else ("0" if change == "NoNewPrivs" else "1")
        )
    raw = "\n".join(k + ":" + v for k, v in values.items()).encode()
    if change:
        with pytest.raises(ValueError):
            proof.require_candidate_status(raw)
    else:
        proof.require_candidate_status(raw)


@pytest.mark.parametrize("option", ["seccomp=unconfined", "apparmor=unconfined"])
def test_unconfined_not_hidden_by_no_new_privileges(option):
    record = inspected(host=True)
    record["HostConfig"]["SecurityOpt"].append(option)
    with pytest.raises(ValueError):
        proof.require_boundary(record, host_pid=True)
