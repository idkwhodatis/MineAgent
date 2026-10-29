"""Offline command construction and exact-parent checks, not Docker builds."""

import json
from pathlib import Path

import pytest

from script import build_oe_structured_images as build


@pytest.mark.parametrize("role", ["controller", "engine"])
def test_rejects_non_iid_parent_before_creating_evidence(tmp_path, role):
    args = {
        "controller_parent": "sha256:" + "a" * 64,
        "engine_parent": "sha256:" + "b" * 64,
    }
    args[role + "_parent"] = "latest"
    with pytest.raises(ValueError):
        build.build(tmp_path / "output", **args)
    assert not (tmp_path / "output").exists()


def test_local_parent_tag_verified_before_and_after_each_offline_build(
    tmp_path, monkeypatch
):
    calls = []
    checks = []
    profiles = []

    def docker(args, **kwargs):
        calls.append(args)
        if args[0] == "build":
            target = Path(args[args.index("--iidfile") + 1])
            target.write_text(
                "sha256:" + ("c" if target.stem == "controller" else "d") * 64
            )

    monkeypatch.setattr(build, "_docker", docker)
    monkeypatch.setattr(
        build,
        "_verify_parent_tag",
        lambda tag, iid: checks.append((tag, iid, len(calls))),
    )
    monkeypatch.setattr(build, "verify_images", profiles.append)
    controller = "sha256:" + "a" * 64
    engine = "sha256:" + "b" * 64
    output = build.build(
        tmp_path / "output", controller_parent=controller, engine_parent=engine
    )
    value = json.loads(output.read_text())
    assert value == profiles[0].model_dump(mode="json")
    assert value["runtime"]["controller_installed_parent_image_id"] == controller
    assert value["runtime"]["engine_installed_parent_image_id"] == engine
    assert [v[2] for v in checks] == [1, 2, 3, 4]
    for command in (calls[1], calls[3]):
        assert command[0:4] == ["build", "--pull=false", "--network", "none"]
        assert "mineagent-local-parent:" in command[-2]
        assert "sha256:" not in command[-2]
    assert checks[0][0] == checks[1][0] and checks[2][0] == checks[3][0]


def test_parent_drift_prevents_profile_publication(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "_docker", lambda *_args, **_kwargs: None)

    def drift(*_):
        raise ValueError("actual parent tag changed")

    monkeypatch.setattr(build, "_verify_parent_tag", drift)
    monkeypatch.setattr(
        build, "verify_images", lambda _: pytest.fail("unchecked image")
    )
    with pytest.raises(ValueError, match="parent tag changed"):
        build.build(
            tmp_path / "output",
            controller_parent="sha256:" + "a" * 64,
            engine_parent="sha256:" + "b" * 64,
        )
    assert not (tmp_path / "output" / "oe-structured-runtime-profile.json").exists()
