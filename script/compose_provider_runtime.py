"""Offline startup checks for explicitly enabled packaged RD provider profiles.

This verifies installed closures and Docker image metadata, never launches a
candidate, calls a provider, grants an admission or enables a disabled profile.
"""

import json
import os
import subprocess
from pathlib import Path

SDK_PYTHON = Path("/opt/mineagent-provider/bin/python")
SDK_VERSIONS = {"openai": "2.54.0", "httpx": "0.28.1"}


def verify_sdk(executable):
    if Path(executable) != SDK_PYTHON or not SDK_PYTHON.is_file():
        raise ValueError("Require the packaged isolated provider SDK interpreter")
    code = "import importlib.metadata as m,json; print(json.dumps({n:m.version(n) for n in ('openai','httpx')}))"
    result = subprocess.run(
        [str(SDK_PYTHON), "-I", "-B", "-c", code],
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/tmp",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=10,
        check=True,
    )
    if len(result.stdout) > 1024 or json.loads(result.stdout) != SDK_VERSIONS:
        raise ValueError("Packaged provider SDK closure differs")


def verify_rd_images(profile):
    from runtimes.factor_execution.verify_inventory import verify
    from runtimes.research_supervisor.analysis_supervisor import AnalysisSupervisor
    from runtimes.research_supervisor.numeric_supervisor import NumericSupervisor
    from workers.research.rdagent_operational import _image_inventory

    verify()  # Exact shipped host source bytes, including seccomp and manifests.
    pins = profile.runtime
    controller = _image_inventory(pins.orchestrator_image)
    if controller["image_id"] != pins.orchestrator_image:
        raise ValueError("Controller image differs")
    for role, image in (
        ("candidate", pins.candidate_image),
        ("normalizer", pins.normalizer_image),
    ):
        if NumericSupervisor(image).verified_inventory(role)["image_id"] != image:
            raise ValueError("Numeric image identity differs")
    AnalysisSupervisor(
        pins.analysis_image, profile.qlib_installed_parent_image_id
    ).verified_inventory("analysis")


def validate_enabled_profiles(settings, role):
    enabled = settings.rdagent_research_enabled and (
        settings.rdagent_live_provider_enabled
        or settings.rdagent_synthetic_provider_enabled
    )
    if not enabled:
        return
    from backend.app.rdagent_provider_profile import resolve_provider_runtime

    profile = resolve_provider_runtime(settings)
    verify_sdk(settings.rdagent_provider_python)
    if not settings.rdagent_provider_profiles:
        raise ValueError("Enabled RD provider requires explicit operator profiles")
    for model in settings.rdagent_provider_profiles:
        transport_enabled = (
            settings.rdagent_synthetic_provider_enabled
            if model.connection.synthetic
            else settings.rdagent_live_provider_enabled
        )
        if transport_enabled and not os.environ.get(model.connection.secret_env):
            raise ValueError("Enabled RD provider credential is unavailable")
    if role in {"research-worker", "evaluator-reconciler"}:
        verify_rd_images(profile)
