"""Pure packaging proofs; installed native-runtime probes remain a separate gate."""

import hashlib
import json
import zipfile
from pathlib import Path

from runtimes.rdagent.build_profile import wheel

ROOT = Path(__file__).resolve().parents[2]


def test_profile_wheel_is_deterministic_and_content_addressed(tmp_path):
    files = {"example/b.py": b"b=2\n", "example/a.py": b"a=1\n"}
    first = wheel(tmp_path, "mineagent_fixture", files, ["example==1.0"])
    original = (tmp_path / first["filename"]).read_bytes()
    second = wheel(
        tmp_path,
        "mineagent_fixture",
        dict(reversed(list(files.items()))),
        ["example==1.0"],
    )
    assert original == (tmp_path / second["filename"]).read_bytes()
    assert first == second
    files["example/a.py"] = b"a=2\n"
    changed = wheel(tmp_path, "mineagent_fixture", files, ["example==1.0"])
    assert changed["sha256"] != first["sha256"]
    with zipfile.ZipFile(tmp_path / changed["filename"]) as archive:
        assert all(
            info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist()
        )


def test_frozen_proof_matches_current_recipe_lock_and_adapter():
    directory = ROOT / "runtimes/rdagent"
    proof = json.loads((directory / "wheel-proof.json").read_text())
    assert (
        proof["recipe_sha256"]
        == hashlib.sha256((directory / "build_profile.py").read_bytes()).hexdigest()
    )
    assert (
        proof["dependency_lock_sha256"]
        == hashlib.sha256((directory / "requirements.lock").read_bytes()).hexdigest()
    )
    assert proof["commit"] == "484776c211e4fbbeef03e0ec00d6bbee7362a4f4"
    for path, expected in proof["adapter"]["files"].items():
        if ".dist-info/" not in path:
            assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == expected
    assert "packages/rdagent_adapter/checkpoint.py" not in proof["adapter"]["files"]
