"""Actual retained dataset staging and full protected-evaluator input manifest."""

import json
import os
from uuid import uuid4

import pytest

from packages.research_runtime.broker_contracts import digest
from tests.openevolve.test_search_staging import fixture
from workers.openevolve.input_inventory import inventory_staged_input
from workers.openevolve.staging import stage_search_input


def stage(tmp_path):
    args = fixture(tmp_path)
    report = stage_search_input(**args)
    return args, report


def inventory(args):
    return inventory_staged_input(
        args["target"],
        evaluation_id=uuid4(),
        plan=args["plan"],
        source_sha256=args["source_sha256"],
        window=args["window"],
    )


def test_real_complete_inventory_and_report(tmp_path):
    args, report = stage(tmp_path)
    value = inventory(args)
    assert value.execution_plan_sha256 == digest(args["plan"])
    assert {"strategy.py", "request.json", "stage-report.json"} <= set(
        f.path for f in value.files
    )
    assert len(value.files) == len(
        [p for p in args["target"].rglob("*") if p.is_file()]
    )
    assert (
        json.loads((args["target"] / "stage-report.json").read_bytes())[
            "point_in_time_proven"
        ]
        is False
    )
    assert report.point_in_time_proven is False
    assert value.window_start == args["window"].start


@pytest.mark.parametrize("mode", ["extra", "hardlink", "symlink", "writable", "empty"])
def test_actual_staged_inventory_rejects_drift(tmp_path, mode):
    args, _ = stage(tmp_path)
    root = args["target"]
    root.chmod(0o755)
    path = root / "strategy.py"
    if mode == "extra":
        (root / "unexpected").write_bytes(b"no")
    elif mode == "writable":
        path.chmod(0o644)
    elif mode == "empty":
        path.chmod(0o644)
        path.write_bytes(b"")
        path.chmod(0o444)
    else:
        other = tmp_path / "other"
        other.write_bytes(path.read_bytes())
        other.chmod(0o444)
        path.unlink()
        if mode == "hardlink":
            os.link(other, path)
        else:
            path.symlink_to(other)
    with pytest.raises((ValueError, OSError)):
        inventory(args)


def test_byte_changes_change_manifest_and_cannot_impersonate_source(tmp_path):
    args, _ = stage(tmp_path)
    path = args["target"] / "strategy.py"
    path.chmod(0o644)
    path.write_bytes(b"x=2\n")
    path.chmod(0o444)
    with pytest.raises(ValueError):
        inventory(args)
