"""027 fixed wrapper contract tests, not Docker/kernel containment acceptance."""

import json

import pytest

from runtimes.openevolve_structured import deadline, profile, trusted_report


def test_distinct_controller_entrypoint_and_exact_fixed_mount_layout():
    assert (
        deadline.PROFILE == profile.PROFILE == "mineagent.oe-structured-orchestrator.v1"
    )
    assert deadline.CHILD_ARGS == (
        "-I",
        "-m",
        "runtimes.openevolve_structured.entrypoint",
    )
    assert profile.inventory()["bootstrap_files"] == ["bootstrap.json", "seed.json"]
    assert profile.inventory()["network"] == "none"
    assert dict(deadline.CHILD_ENV).get("PYTHONPATH") is None
    assert not any("KEY" in key or "TOKEN" in key for key, _ in deadline.CHILD_ENV)


@pytest.mark.parametrize(
    "deadline_ms,now",
    [("1000", 1000), ("999", 1000), ("601001", 1000), ("1.0", 0), ("1000", True)],
)
def test_original_controller_deadline_rejects_expired_overlong_or_coerced(
    deadline_ms, now
):
    with pytest.raises(ValueError):
        deadline.remaining_seconds(deadline_ms, now)


def test_trusted_report_is_new_protocol_and_never_positive_host_stop():
    raw = trusted_report.report_bytes("quiescent", 2, 123, 0)
    value = json.loads(raw)
    assert value["schema"] == "mineagent.oe-structured-controller-report.v1"
    assert value["descendants_reaped"] is True
    assert not any(
        key in value
        for key in ("confirmed_absent", "kernel_identity_absent", "container_id")
    )
