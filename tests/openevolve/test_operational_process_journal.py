"""Actual host journal bytes; no kernel/Docker launch proof."""

from dataclasses import replace

import pytest

from packages.research_runtime.process_contracts import ProcessError, canonical, digest
from runtimes.openevolve_operational import process_journal as module
from tests.openevolve.operational_fixtures import records


def persist(root, values):
    launch = values[0]
    directory = (
        root
        / "research-processes"
        / str(launch.intent.admission.owner_id)
        / str(launch.intent.admission.id)
        / "control"
    )
    directory.mkdir(parents=True)
    for name, value in zip(
        ("launch", "identity", "child", "stop"), values, strict=True
    ):
        path = directory / (name + ".json")
        path.write_bytes(canonical(value.model_dump(mode="json")))
        path.chmod(0o600)
    return directory


def test_full_actual_journal_roundtrip(tmp_path):
    values = records(tmp_path)
    persist(tmp_path, values)
    assert (
        module.read_controller_journal(
            tmp_path, values[0].intent.admission, with_child=True, with_stop=True
        )
        == values
    )


@pytest.mark.parametrize(
    "field",
    ["bootstrap_sha256", "seed_sha256", "seed_size_bytes", "bootstrap_size_bytes"],
)
def test_complete_two_file_commitment_not_single_bootstrap(tmp_path, field):
    intent = records(tmp_path)[0].intent
    value = "0" * 64 if "sha256" in field else 1
    with pytest.raises(ValueError):
        module.ControllerIntent.model_validate(intent.model_copy(update={field: value}))


def test_wrong_child_parent_cannot_bind(tmp_path):
    launch, identity, child, _ = records(tmp_path)
    changed = child.model_copy(
        update={"controller": replace(child.controller, ppid=999)}
    )
    with pytest.raises(ProcessError):
        module.validate_records(launch, identity, changed)


def test_stop_without_child_only_if_no_child_retained(tmp_path):
    launch, identity, child, stop = records(tmp_path)
    stop = stop.model_copy(
        update={"child_sha256": None, "child_kernel_identity_absent": None}
    )
    directory = persist(tmp_path, (launch, identity, child, stop))
    with pytest.raises(ProcessError):
        module.read_controller_journal(
            tmp_path, launch.intent.admission, with_stop=True
        )
    (directory / "child.json").unlink()
    assert (
        module.read_controller_journal(
            tmp_path, launch.intent.admission, with_stop=True
        )[2]
        is None
    )


def test_stale_root_inode_rejected(tmp_path):
    launch, identity, child, stop = records(tmp_path)
    launch = launch.model_copy(update={"root_inode": launch.root_inode + 1})
    identity = identity.model_copy(
        update={
            "root_inode": launch.root_inode,
            "launch_sha256": digest(launch.model_dump(mode="json")),
        }
    )
    child = child.model_copy(
        update={"identity_sha256": digest(identity.model_dump(mode="json"))}
    )
    stop = stop.model_copy(
        update={
            "launch_sha256": identity.launch_sha256,
            "identity_sha256": child.identity_sha256,
            "child_sha256": digest(child.model_dump(mode="json")),
        }
    )
    persist(tmp_path, (launch, identity, child, stop))
    with pytest.raises(ProcessError):
        module.read_controller_journal(
            tmp_path, launch.intent.admission, with_stop=True
        )
