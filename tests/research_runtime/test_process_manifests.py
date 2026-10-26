"""Review proposals cannot activate a new engine or mutate N1 contracts."""

from dataclasses import FrozenInstanceError, replace

import pytest

from packages.research_runtime.process_manifests import (
    PROPOSALS,
    InputBinding,
    propose_manifest,
    validate_manifest,
)
from packages.research_runtime.process_profiles import PROFILES

IMAGE = "sha256:" + "a" * 64


def make(profile="openevolve_orchestrator.proposal.v1"):
    inputs = tuple(
        InputBinding(path, "b" * 64, 12) for path, _ in PROPOSALS[profile].input_limits
    )
    return propose_manifest(profile, IMAGE, inputs, trusted_image_pins={profile: IMAGE})


@pytest.mark.parametrize("profile", tuple(PROPOSALS))
def test_disabled_canonical_manifest_roundtrip(profile):
    manifest = make(profile)
    value = manifest.public()
    restored = validate_manifest(value, trusted_image_pins={profile: IMAGE})
    assert restored == manifest
    assert restored.sha256 == manifest.sha256
    assert restored.minimum_reservation.process_slots == 1
    assert restored.minimum_reservation.storage_bytes > sum(
        size for _, size in restored.resources.tmpfs
    )
    with pytest.raises(ValueError, match="never launch"):
        restored.require_launch_authority()
    assert profile not in PROFILES


def test_nested_immutability_and_detached_json():
    manifest = make()
    with pytest.raises(FrozenInstanceError):
        manifest.resources.pids = 100
    with pytest.raises(TypeError):
        manifest.environment[0] = ("SECRET", "value")
    original = manifest.sha256
    value = manifest.public()
    value["resources"]["pids"] = 100
    value["inputs"][0]["sha256"] = "c" * 64
    assert manifest.sha256 == original


@pytest.mark.parametrize(
    "field,value",
    [
        ("profile", "arbitrary.v1"),
        ("role", "factor_cutoff_candidate"),
        ("image_id", "python:latest"),
        ("admission_status", "admitted"),
        ("schema_version", "mineagent.research-process.v1"),
        ("executable", ["sh", "-c", "anything"]),
        ("environment", [["PROVIDER_API_KEY", "secret"]]),
        ("environment_inheritance", True),
        ("network", "host"),
        ("root_readonly", False),
        ("capabilities", ["SYS_ADMIN"]),
        ("input_readonly", False),
        ("unresolved", []),
    ],
)
def test_reject_top_level_drift(field, value):
    manifest = make()
    wire = manifest.public()
    wire[field] = value
    with pytest.raises(ValueError):
        validate_manifest(wire, trusted_image_pins={manifest.profile: IMAGE})


@pytest.mark.parametrize(
    "mutate",
    [
        lambda wire: wire["isolation"].update(privileged=True),
        lambda wire: wire["isolation"].update(pid_namespace="host"),
        lambda wire: wire["isolation"].update(devices=["/dev/sda"]),
        lambda wire: wire["isolation"].update(no_new_privileges=False),
        lambda wire: wire["resources"].update(pids=True),
        lambda wire: wire["resources"].update(wall_milliseconds=1),
        lambda wire: wire["resources"].update(shm_bytes=0),
        lambda wire: wire["resources"].update(journal_bytes=0),
        lambda wire: wire["resources"].update(extraction_bytes=0),
        lambda wire: wire["resources"].update(publication_copy_bytes=0),
        lambda wire: wire["resources"].update(tmpfs=[["/var/run/docker.sock", 1024]]),
        lambda wire: wire["ipc"].update(peer_scope="self_reported_pid"),
        lambda wire: wire["inputs"][0].update(destination="/bootstrap/../secret"),
        lambda wire: wire["inputs"][0].update(size_bytes=True),
        lambda wire: wire["inputs"][0].update(size_bytes=2**30),
        lambda wire: wire["inputs"][0].update(sha256="invalid"),
        lambda wire: wire["inputs"].append(wire["inputs"][0]),
        lambda wire: wire["inputs"].pop(),
        lambda wire: wire.update(callback="module.function"),
    ],
)
def test_reject_nested_drift(mutate):
    manifest = make()
    wire = manifest.public()
    mutate(wire)
    with pytest.raises((ValueError, RuntimeError)):
        validate_manifest(wire, trusted_image_pins={manifest.profile: IMAGE})


def test_image_configuration_and_environment_do_not_grant_authority(monkeypatch):
    manifest = make()
    monkeypatch.setenv("MINEAGENT_RESEARCH_SUPERVISOR_IMAGE", IMAGE)
    monkeypatch.setenv("PROVIDER_API_KEY", "must-not-inherit")
    with pytest.raises(ValueError):
        validate_manifest(manifest.public(), trusted_image_pins={})
    with pytest.raises(ValueError):
        validate_manifest(
            manifest.public(),
            trusted_image_pins={manifest.profile: "sha256:" + "c" * 64},
        )
    assert "must-not-inherit" not in str(make().public())


def test_no_candidate_ipc_and_full_peak_accounting():
    manifest = make("factor_cutoff_candidate.proposal.v1")
    assert manifest.ipc.mode == "none"
    assert manifest.ipc.mount is None
    assert not manifest.ipc.purposes
    assert "hostile_child_cannot_disable_deadline" in manifest.unresolved
    r = manifest.resources
    expected = (
        sum(size for _, size in r.tmpfs)
        + r.shm_bytes
        + r.journal_bytes
        + r.extraction_bytes
        + r.publication_copy_bytes
        + 2 * sum(item.size_bytes for item in manifest.inputs)
    )
    assert manifest.minimum_reservation.storage_bytes == expected
    with pytest.raises(ValueError):
        replace(manifest, ipc=make().ipc)


def test_input_content_changes_identity_and_reservation():
    first = make()
    changed = replace(first.inputs[0], sha256="c" * 64, size_bytes=13)
    second = replace(first, inputs=(changed,) + first.inputs[1:])
    assert second.sha256 != first.sha256
    assert second.input_sha256 != first.input_sha256
    assert (
        second.minimum_reservation.storage_bytes
        == first.minimum_reservation.storage_bytes + 2
    )


def test_n1_registry_and_serialized_admission_unchanged():
    from uuid import UUID

    from packages.research_runtime.process_contracts import ProcessAdmission, canonical

    assert tuple(PROFILES) == ("neutral_fixture.v1",)
    assert PROFILES["neutral_fixture.v1"].reservation.storage_bytes == 1179648
    uid = UUID(int=1)
    admission = ProcessAdmission(
        uid,
        uid,
        uid,
        uid,
        1,
        "neutral_fixture",
        uid,
        1,
        "neutral_fixture.v1",
        "a" * 64,
        uid,
        IMAGE,
        "b" * 64,
    )
    before = canonical(admission.public())
    make()
    assert canonical(admission.public()) == before
    assert "manifest" not in admission.public()
    with pytest.raises(ValueError):
        validate_manifest(admission.public(), trusted_image_pins={})


def test_frozen_image_inventory_requires_independent_review_for_roundtrip():
    from packages.research_runtime.process_manifests import ImageConfigInventory

    manifest = make()
    image = ImageConfigInventory(
        IMAGE, (("PATH", "/usr/local/bin:/usr/bin:/bin"),), "/opt/mineagent"
    )
    manifest = replace(manifest, image_config=image)
    with pytest.raises(ValueError):
        validate_manifest(
            manifest.public(), trusted_image_pins={manifest.profile: IMAGE}
        )
    restored = validate_manifest(
        manifest.public(),
        trusted_image_pins={manifest.profile: IMAGE},
        trusted_image_configs={IMAGE: image},
    )
    assert restored == manifest
    with pytest.raises(ValueError):
        replace(manifest, image_config=replace(image, image_id="sha256:" + "c" * 64))
    with pytest.raises(ValueError):
        ImageConfigInventory(
            IMAGE, (("PATH", "/bin"), ("PROVIDER_API_KEY", "secret")), ""
        )
    assert "PYTHONHASHSEED" not in dict(manifest.environment)


def test_hdf_normalizer_disables_dynamic_plugins_not_merely_search_path():
    from packages.research_runtime.process_manifests import PROPOSALS

    environment = dict(PROPOSALS["factor_hdf_normalizer.proposal.v1"].environment)
    assert environment["HDF5_PLUGIN_PRELOAD"] == "::"
