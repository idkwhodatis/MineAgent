"""Packaged source/startup contracts; not Docker or provider acceptance."""

import subprocess
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import yaml

from script import compose_provider_runtime as runtime
from script.build_rd_compose_images import profile_from_ids, read_iid

ROOT = Path(__file__).resolve().parents[2]


def test_sdk_is_separate_pinned_stage_in_both_admission_and_worker_images():
    source = (ROOT / "infra/application.Dockerfile").read_text()
    assert (
        "@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e"
        in source
    )
    sdk = source.split("AS provider-sdk", 1)[1].split("FROM", 1)[0]
    assert "runtimes/rdagent_provider/requirements.lock" in sdk
    assert "--require-hashes" in sdk and "--only-binary=:all:" in sdk
    assert "--copies /opt/mineagent-provider" in sdk
    for role in ("api", "research"):
        stage = source.split(f"FROM source AS {role}", 1)[1].split("FROM", 1)[0]
        assert (
            "COPY --from=provider-sdk /opt/mineagent-provider /opt/mineagent-provider"
            in stage
        )
        assert f"validate_runtime_split --runtime {role}" in stage
    assert "openai==2.6.0" in (ROOT / "runtimes/research/requirements.in").read_text()


def test_optional_profiles_are_identical_in_admission_dispatch_worker_and_recovery():
    services = yaml.safe_load((ROOT / "infra/compose.full.yml").read_text())["services"]
    names = {"api", "research-dispatcher", "research-worker", "evaluator-reconciler"}
    profiles = []
    for role, service in services.items():
        values = {
            k: v
            for k, v in service.get("environment", {}).items()
            if k.startswith("RDAGENT_")
        }
        if role in names:
            profiles.append(values)
            assert values["RDAGENT_PROVIDER_PYTHON"] == str(runtime.SDK_PYTHON)
            for key in (
                "RDAGENT_RESEARCH_ENABLED",
                "RDAGENT_LIVE_PROVIDER_ENABLED",
                "RDAGENT_SYNTHETIC_PROVIDER_ENABLED",
            ):
                assert values[key] == "${" + key + ":-false}"
        else:
            assert not values
    assert all(value == profiles[0] for value in profiles)
    overlay = yaml.safe_load((ROOT / "infra/compose.providers.yml").read_text())[
        "services"
    ]
    assert set(overlay) == names
    for name, service in overlay.items():
        trusted_host = name in {"research-worker", "evaluator-reconciler"}
        assert set(service) == ({"env_file", "pid"} if trusted_host else {"env_file"})
        if trusted_host:
            assert service["pid"] == "host"


def test_exact_host_inventory_inputs_survive_application_default_deny():
    from runtimes.factor_execution.verify_inventory import FILES

    rules = (
        (ROOT / "infra/application.Dockerfile.dockerignore").read_text().splitlines()
    )
    for name in (*FILES, "runtimes/factor_execution/runtime-draft-inventory.json"):
        if not (name.endswith((".py", ".lock", "/Dockerfile"))):
            assert "!" + name in rules
    assert "!runtimes/**/*.json" not in rules
    assert not any(value.startswith(("!.env", "!var/")) for value in rules)


def test_sdk_probe_uses_isolated_interpreter_no_credentials_or_network_code(
    monkeypatch,
):
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return NS(stdout=b'{"openai":"2.54.0","httpx":"0.28.1"}')

    monkeypatch.setattr(subprocess, "run", run)
    runtime.verify_sdk(runtime.SDK_PYTHON)
    command, options = calls[0]
    assert command[:4] == [str(runtime.SDK_PYTHON), "-I", "-B", "-c"]
    assert set(options["env"]) == {"PATH", "HOME", "PYTHONDONTWRITEBYTECODE"}
    assert options["timeout"] == 10 and options["stdin"] is subprocess.DEVNULL
    with pytest.raises(ValueError):
        runtime.verify_sdk("/tmp/host-venv/bin/python")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: NS(stdout=b'{"openai":"2.6.0","httpx":"0.28.1"}'),
    )
    with pytest.raises(ValueError):
        runtime.verify_sdk(runtime.SDK_PYTHON)


def test_disabled_profiles_do_not_probe_or_enable_any_runtime(monkeypatch):
    monkeypatch.setattr(runtime, "verify_sdk", lambda *a: pytest.fail("disabled SDK"))
    runtime.validate_enabled_profiles(NS(rdagent_research_enabled=False), "api")


def test_api_has_no_docker_probe_worker_reuses_existing_verified_inventory(monkeypatch):
    import backend.app.rdagent_provider_profile as profiles

    settings = NS(
        rdagent_research_enabled=True,
        rdagent_live_provider_enabled=False,
        rdagent_synthetic_provider_enabled=True,
        rdagent_provider_python=runtime.SDK_PYTHON,
        rdagent_provider_profiles=(
            NS(connection=NS(synthetic=True, secret_env="TEST_ONLY_SECRET")),
        ),
    )
    monkeypatch.setenv("TEST_ONLY_SECRET", "inert")
    monkeypatch.setattr(profiles, "resolve_provider_runtime", lambda value: "profile")
    calls = []
    monkeypatch.setattr(runtime, "verify_sdk", lambda value: calls.append("sdk"))
    monkeypatch.setattr(
        runtime, "verify_rd_images", lambda value: calls.append("images")
    )
    runtime.validate_enabled_profiles(settings, "api")
    assert calls == ["sdk"]
    runtime.validate_enabled_profiles(settings, "research-dispatcher")
    assert calls == ["sdk", "sdk"]
    runtime.validate_enabled_profiles(settings, "research-worker")
    assert calls == ["sdk", "sdk", "sdk", "images"]
    entry = (ROOT / "script/compose_runtime.py").read_text()
    assert 'args.role in {"api", "research-dispatcher", *SUPERVISORS}' in entry


def test_profile_uses_exact_built_ids_and_shipped_locks(tmp_path):
    ids = {
        role: "sha256:" + str(index) * 64
        for index, role in enumerate(
            ("orchestrator", "candidate", "normalizer", "analysis", "qlib-installed"), 1
        )
    }
    profile = profile_from_ids(ids)
    assert profile.runtime.orchestrator_image == ids["orchestrator"]
    assert profile.qlib_installed_parent_image_id == ids["qlib-installed"]
    path = tmp_path / "image.iid"
    path.write_text("python:latest")
    with pytest.raises(ValueError):
        read_iid(path)
    with pytest.raises(ValueError):
        profile_from_ids({**ids, "candidate": "python:latest"})


def test_build_targets_force_local_daemon_and_do_not_inherit_provider_environment(
    tmp_path, monkeypatch
):
    from script import build_rd_compose_images as builder

    monkeypatch.setattr(builder, "verify_prepared_inputs", lambda: None)
    monkeypatch.setattr(builder, "verify_rd_images", lambda profile: None)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        assert command[:5] == [
            "docker",
            "--config",
            "/nonexistent",
            "--host",
            "unix:///var/run/docker.sock",
        ]
        assert set(kwargs["env"]) == {"PATH", "HOME"}
        assert kwargs["timeout"] == 1800
        Path(command[command.index("--iidfile") + 1]).write_text(
            "sha256:" + str(len(calls)) * 64
        )

    monkeypatch.setattr(builder.subprocess, "run", run)
    path = builder.build(tmp_path / "new-build")
    assert path.is_file() and len(calls) == 5
    assert "--network" in calls[-1][0]
    assert "QLIB_IMAGE=sha256:" + "4" * 64 in calls[-1][0]
