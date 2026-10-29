"""Availability diagnostics exercise original guards with inert CLI responses."""

import json
import subprocess

import pytest

from tests.portable.availability_diagnostics import (
    DiagnosticPortableDockerEvaluator,
    assert_available,
)

SECRET = "PRIVATE_TOKEN=/private/credentials/docker.sock"
INFO = {
    "OSType": "linux",
    "CgroupVersion": "2",
    "CgroupDriver": "systemd",
    "SecurityOptions": ["name=seccomp"],
    **dict.fromkeys(
        ("MemoryLimit", "SwapLimit", "CpuCfsPeriod", "CpuCfsQuota", "PidsLimit"), True
    ),
}
IMAGE = "sha256:" + "a" * 64


def setup_probe(tmp_path, monkeypatch, responses):
    runner = DiagnosticPortableDockerEvaluator(
        docker_binary="fake-docker",
        state_dir=tmp_path / "state",
        engine_image="private-engine",
        policy_image="private-policy",
    )
    calls = []

    def command(args, **kwargs):
        calls.append((args, kwargs))
        response = responses[len(calls) - 1]
        if isinstance(response, Exception):
            raise response
        code, output = response
        return subprocess.CompletedProcess(args, code, output, SECRET)

    monkeypatch.setattr(subprocess, "run", command)
    return runner, calls


@pytest.mark.parametrize(
    "responses,stage,reason,code",
    [
        ([(0, SECRET)], "daemon_info", "verification_rejected", 0),
        ([(0, "[]")], "daemon_info", "verification_rejected", 0),
        ([(1, json.dumps(INFO))], "daemon_info", "command_nonzero", 1),
        (
            [(0, json.dumps({**INFO, "PidsLimit": False}))],
            "daemon_info",
            "verification_rejected",
            0,
        ),
        ([(0, json.dumps(INFO)), (1, SECRET)], "engine_image", "command_nonzero", 1),
        (
            [(0, json.dumps(INFO)), (0, SECRET)],
            "engine_image",
            "verification_rejected",
            0,
        ),
        (
            [(0, json.dumps(INFO)), (0, IMAGE), (125, SECRET)],
            "policy_image",
            "command_nonzero",
            125,
        ),
        (
            [subprocess.TimeoutExpired(SECRET, 10, output=SECRET, stderr=SECRET)],
            "daemon_info",
            "command_timeout",
            None,
        ),
        ([OSError(SECRET)], "daemon_info", "command_os_error", None),
        ([(999999, SECRET)], "daemon_info", "command_nonzero", None),
    ],
)
def test_original_available_failure_is_allowlisted_without_retry_or_disclosure(
    tmp_path, monkeypatch, responses, stage, reason, code
):
    runner, calls = setup_probe(tmp_path, monkeypatch, responses)
    result = runner.availability_diagnostic()
    assert result == {
        "available": False,
        "stage": stage,
        "reason": reason,
        "exit_code": code,
    }
    assert len(calls) == len(responses)
    assert all(kwargs["timeout"] == 10 for _, kwargs in calls)
    assert SECRET not in json.dumps(result)
    assert "private-engine" not in json.dumps(result)
    assert runner._availability_probe is False


def test_success_requires_both_images_and_complete_original_guard(
    tmp_path, monkeypatch
):
    runner, calls = setup_probe(
        tmp_path, monkeypatch, [(0, json.dumps(INFO)), (0, IMAGE), (0, IMAGE)]
    )
    assert runner.availability_diagnostic() == {
        "available": True,
        "stage": "complete",
        "reason": "ready",
        "exit_code": 0,
    }
    assert len(calls) == 3


def test_missing_binary_classification_without_cli_or_leak(tmp_path, monkeypatch):
    runner, calls = setup_probe(tmp_path, monkeypatch, [])
    runner.binary = None
    assert runner.availability_diagnostic() == {
        "available": False,
        "stage": "daemon_info",
        "reason": "command_unavailable",
        "exit_code": None,
    }
    assert calls == []


def test_fixture_failure_exposes_only_sanitized_diagnostic(tmp_path, monkeypatch):
    runner, calls = setup_probe(tmp_path, monkeypatch, [(1, SECRET)])
    with pytest.raises(pytest.fail.Exception) as exc:
        assert_available(runner)
    assert json.loads(str(exc.value)) == {
        "available": False,
        "stage": "daemon_info",
        "reason": "command_nonzero",
        "exit_code": 1,
    }
    assert exc.value.pytrace is False
    assert SECRET not in str(exc.value)
    assert len(calls) == 1


def test_observer_is_disabled_after_probe_for_normal_commands(tmp_path, monkeypatch):
    runner, calls = setup_probe(tmp_path, monkeypatch, [(1, SECRET), (0, SECRET)])
    diagnostic = runner.availability_diagnostic()
    runner._command("inspect", "private-name", timeout=3)
    assert runner._availability_result == {
        k: v for k, v in diagnostic.items() if k != "available"
    }
    assert calls[-1][1]["timeout"] == 3


@pytest.mark.parametrize(
    "image_responses,stage",
    [([(1, SECRET)], "engine_image"), ([(0, IMAGE), (1, SECRET)], "policy_image")],
)
def test_equal_image_references_keep_correct_probe_stage(
    tmp_path, monkeypatch, image_responses, stage
):
    runner, calls = setup_probe(
        tmp_path, monkeypatch, [(0, json.dumps(INFO)), *image_responses]
    )
    runner.images["policy"] = runner.images["engine"]
    assert runner.availability_diagnostic()["stage"] == stage
    assert len(calls) == len(image_responses) + 1


def test_unexpected_exception_does_not_leave_probe_enabled(tmp_path, monkeypatch):
    runner, calls = setup_probe(tmp_path, monkeypatch, [RuntimeError("synthetic")])
    with pytest.raises(RuntimeError, match="synthetic"):
        runner.availability_diagnostic()
    assert runner._availability_probe is False
    assert len(calls) == 1


@pytest.mark.parametrize(
    "field,bad",
    [
        ("OSType", "other"),
        ("CgroupVersion", "1"),
        ("CgroupDriver", "other"),
        ("SecurityOptions", []),
        *[
            (key, False)
            for key in (
                "MemoryLimit",
                "SwapLimit",
                "CpuCfsPeriod",
                "CpuCfsQuota",
                "PidsLimit",
            )
        ],
    ],
)
def test_all_original_isolation_capability_requirements_still_reject(
    tmp_path, monkeypatch, field, bad
):
    runner, calls = setup_probe(
        tmp_path, monkeypatch, [(0, json.dumps({**INFO, field: bad}))]
    )
    assert runner.availability_diagnostic()["available"] is False
    assert len(calls) == 1
