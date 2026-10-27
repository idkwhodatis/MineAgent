"""Reviewed 024 recipe commitments, not a signature over arbitrary images.

The required image is built from the pinned Dockerfile and measured by image ID.
These fields bind its dependency lock, phase8 chained patch manifest and fixed
prompt/config construction recipe. They do not authorize caller-chosen images.
"""

import hashlib
from pathlib import Path

PINS = {
    "runtime_lock_sha256": (
        "runtimes/openevolve/requirements.lock",
        "c351f13fcbcdc43610d3b0b4846223e58440abb6301fe855a7cba548bee711f6",
    ),
    "adapter_patch_sha256": (
        "runtimes/openevolve_phase8/manifest.json",
        "7eb24236ba279b25978640faf9eb249bad6d0c362b2deac9a5beb51189ff574d",
    ),
    "prompt_profile_sha256": (
        "packages/openevolve_adapter/runtime_config.py",
        "3d2e74fc049e008807fea381b4079d48ef98eff1fbbb1018fad01ec7258eb437",
    ),
}


def require_declared_recipe_pins(spec):
    if any(getattr(spec, name) != expected for name, (_, expected) in PINS.items()):
        raise ValueError("Unregistered initial controller recipe commitment")


def verify_recipe_pins(spec, *, root=None):
    require_declared_recipe_pins(spec)
    root = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    for path, expected in PINS.values():
        raw = (root / path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("Reviewed controller recipe source drift")
