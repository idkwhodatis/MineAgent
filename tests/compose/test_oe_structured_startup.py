"""Normal027 startup metadata contracts; no Docker/provider execution claim."""

from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import yaml

from script import compose_oe_structured_runtime as runtime

ROOT = Path(__file__).resolve().parents[2]


def settings(**changes):
    values = {
        "oe_structured_research_enabled": True,
        "oe_structured_live_provider_enabled": False,
        "oe_structured_synthetic_provider_enabled": True,
        "oe_structured_provider_python": "/opt/mineagent-provider/bin/python",
        "oe_structured_provider_profiles": (
            NS(connection=NS(id="local", synthetic=True, secret_env="LOCAL_TEST_KEY")),
        ),
    }
    return NS(**{**values, **changes})


def test_same_default_closed_normal_configuration_on_exact_trusted_roles():
    services = yaml.safe_load((ROOT / "infra/compose.full.yml").read_text())["services"]
    names = {"api", "research-dispatcher", "research-worker", "evaluator-reconciler"}
    declarations = []
    for name, service in services.items():
        config = {
            k: v
            for k, v in service.get("environment", {}).items()
            if k.startswith("OE_STRUCTURED_")
        }
        if name not in names:
            assert not config
            continue
        assert len(config) == 6
        assert (
            config["OE_STRUCTURED_PROVIDER_PYTHON"]
            == "/opt/mineagent-provider/bin/python"
        )
        for k in (
            "OE_STRUCTURED_RESEARCH_ENABLED",
            "OE_STRUCTURED_LIVE_PROVIDER_ENABLED",
            "OE_STRUCTURED_SYNTHETIC_PROVIDER_ENABLED",
        ):
            assert config[k] == "${" + k + ":-false}"
        declarations.append(config)
    assert len(declarations) == 4 and all(c == declarations[0] for c in declarations)


def test_disabled_normal_path_does_not_inspect_sdk_images_or_credentials(monkeypatch):
    monkeypatch.setattr(runtime, "verify_sdk", lambda *_: pytest.fail("disabled SDK"))
    monkeypatch.setattr(
        runtime, "verify_images", lambda *_: pytest.fail("disabled image")
    )
    runtime.validate_enabled_profiles(NS(), "research-worker")
    runtime.validate_enabled_profiles(
        settings(oe_structured_research_enabled=False), "research-worker"
    )
    runtime.validate_enabled_profiles(
        settings(oe_structured_synthetic_provider_enabled=False), "research-worker"
    )


@pytest.mark.parametrize(
    "role,images",
    [
        ("api", False),
        ("research-dispatcher", False),
        ("research-worker", True),
        ("evaluator-reconciler", True),
    ],
)
def test_enabled_normal_checks_exact_sdk_and_only_trusted_host_images(
    monkeypatch, role, images
):
    from backend.app import oe_structured_server_profile

    profile = object()
    calls = []
    monkeypatch.setattr(
        oe_structured_server_profile, "resolve_runtime", lambda _: profile
    )
    monkeypatch.setattr(
        runtime, "verify_sdk", lambda value: calls.append(("sdk", value))
    )
    monkeypatch.setattr(
        runtime, "verify_images", lambda value: calls.append(("images", value))
    )
    monkeypatch.setenv("LOCAL_TEST_KEY", "synthetic-not-a-provider-key")
    runtime.validate_enabled_profiles(settings(), role)
    assert calls == [("sdk", "/opt/mineagent-provider/bin/python")] + (
        [("images", profile)] if images else []
    )


@pytest.mark.parametrize("drift", ["no_profiles", "duplicate", "missing_secret"])
def test_enabled_normal_invalid_operator_configuration_fails_closed(monkeypatch, drift):
    from backend.app import oe_structured_server_profile

    monkeypatch.setattr(
        oe_structured_server_profile, "resolve_runtime", lambda _: object()
    )
    monkeypatch.setattr(runtime, "verify_sdk", lambda _: None)
    monkeypatch.setattr(
        runtime,
        "verify_images",
        lambda _: pytest.fail("invalid profile inspected images"),
    )
    monkeypatch.setenv("LOCAL_TEST_KEY", "synthetic-not-a-provider-key")
    config = settings()
    if drift == "no_profiles":
        config.oe_structured_provider_profiles = ()
    elif drift == "duplicate":
        config.oe_structured_provider_profiles *= 2
    else:
        monkeypatch.delenv("LOCAL_TEST_KEY")
    with pytest.raises(ValueError):
        runtime.validate_enabled_profiles(config, "research-worker")


def test_packaged_host_includes_exact_non_python_engine_inventory_inputs():
    source = (ROOT / "infra/application.Dockerfile.dockerignore").read_text()
    for name in (
        "runtimes/structured_factor_engine/Dockerfile.dockerignore",
        "runtimes/structured_factor_engine/trusted_parent.pth",
    ):
        assert "!" + name in source
        assert (ROOT / name).is_file()
    assert (
        "validate_oe_profiles(get_settings(), args.role)"
        in (ROOT / "script/compose_runtime.py").read_text()
    )


def test_exact_normal_image_verifiers_reused_without_launch(monkeypatch):
    from backend.app.oe_structured_epoch_services import HOOK_MANIFEST_SHA256
    from runtimes.research_supervisor import structured_factor_supervisor
    from runtimes.structured_factor_engine import recipe
    from workers.research import oe_structured_controller

    calls = []
    pins = NS(
        controller_inventory_sha256=HOOK_MANIFEST_SHA256,
        engine_dependency_lock_sha256=recipe.dependency_lock_sha256(),
        controller_image_id="sha256:" + "a" * 64,
        engine_image_id="sha256:" + "b" * 64,
        engine_installed_parent_image_id="sha256:" + "c" * 64,
    )
    monkeypatch.setattr(
        oe_structured_controller,
        "image_inventory",
        lambda p: (
            calls.append(("controller", p)) or {"image_id": p.controller_image_id}
        ),
    )

    class Supervisor:
        def __init__(self, image, parent):
            assert (image, parent) == (
                pins.engine_image_id,
                pins.engine_installed_parent_image_id,
            )

        def verified_inventory(self, role):
            calls.append(("engine", role))
            return {"image_id": pins.engine_image_id}

    monkeypatch.setattr(
        structured_factor_supervisor, "StructuredSupervisor", Supervisor
    )
    runtime.verify_images(NS(runtime=pins))
    assert calls == [("controller", pins), ("engine", "engine")]
    pins.controller_inventory_sha256 = "0" * 64
    with pytest.raises(ValueError):
        runtime.verify_images(NS(runtime=pins))
    assert len(calls) == 2
