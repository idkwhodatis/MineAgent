"""Offline immutable source inventory checks; no launch/registry authority."""

import hashlib
import json
from pathlib import Path

FILES = (
    "runtimes/hostile_deadline/contract.py",
    "runtimes/hostile_deadline/deadline.py",
    "runtimes/hostile_deadline/seccomp.default.json",
    "runtimes/hostile_deadline/acceptance.py",
    "runtimes/hostile_deadline/attach_stream.py",
    "runtimes/hostile_deadline/protected_output.py",
    "runtimes/factor_execution/Dockerfile",
    "runtimes/factor_execution/Dockerfile.dockerignore",
    "runtimes/factor_execution/requirements.lock",
    "runtimes/factor_execution/abi_fixture.py",
    "runtimes/factor_execution/adverse_fixture.py",
    "runtimes/factor_execution/adverse_acceptance.py",
    "runtimes/factor_execution/numeric_deadline.py",
    "runtimes/factor_execution/probe_dependencies.py",
    "runtimes/factor_execution/candidate_deadline.py",
    "runtimes/factor_execution/protected_output.py",
    "runtimes/factor_execution/attach_stream.py",
    "runtimes/factor_execution/runtime_contract.py",
    "runtimes/factor_execution/acceptance.py",
    "runtimes/factor_execution/verify_inventory.py",
    "runtimes/factor_normalizer/Dockerfile",
    "runtimes/factor_normalizer/Dockerfile.dockerignore",
    "runtimes/factor_normalizer/requirements.lock",
    "runtimes/factor_normalizer/normalize.py",
    "runtimes/factor_normalizer/entrypoint.py",
    "runtimes/factor_normalizer/protected_deadline.py",
    "packages/rdagent_adapter/assembly.py",
    "packages/rdagent_adapter/host_evidence.py",
    "packages/rdagent_adapter/numeric_profile.py",
    "packages/research_runtime/process_manifests.py",
)


def verify():
    root = Path(__file__).resolve().parents[2]
    path = Path(__file__).with_name("runtime-draft-inventory.json")
    raw = path.read_bytes()
    manifest = json.loads(raw)
    if manifest.get("admission_status") != "disabled" or set(manifest["files"]) != set(
        FILES
    ):
        raise ValueError("numeric immutable inventory differs")
    for name in FILES:
        source = root / name
        if any(parent.is_symlink() for parent in (source, *source.parents)):
            raise ValueError("symlink in numeric source inventory")
        if hashlib.sha256(source.read_bytes()).hexdigest() != manifest["files"][name]:
            raise ValueError("numeric source digest differs: " + name)
    return {
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "files": manifest["files"],
        "admission_status": "disabled",
    }


if __name__ == "__main__":
    print(json.dumps(verify(), sort_keys=True))
