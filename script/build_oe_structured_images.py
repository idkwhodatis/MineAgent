"""Build two normal027 derivatives from explicit existing local parent IIDs.

No dependency download, profile enablement, service start or provider request.
Parents must already be built from the reviewed upstream/engine recipes. Exact
local tags are only Docker FROM transport aliases, never published profile pins.
"""

import argparse
import json
import re
from pathlib import Path

from backend.app.oe_structured_epoch_services import HOOK_MANIFEST_SHA256
from backend.app.oe_structured_server_schemas import OEStructuredServerProfile
from packages.openevolve_structured.provider_run import StructuredRuntimePins
from runtimes.structured_factor_engine.recipe import dependency_lock_sha256
from script.build_rd_compose_images import _docker, _verify_parent_tag, read_iid
from script.compose_oe_structured_runtime import verify_images

TARGETS = (
    (
        "controller",
        "runtimes/openevolve_structured/Dockerfile.provider",
        "OPENEvolve_PARENT_IMAGE",
    ),
    ("engine", "runtimes/structured_factor_engine/Dockerfile", "TRUSTED_ENGINE_IMAGE"),
)


def build(output, *, controller_parent, engine_parent):
    parents = {"controller": controller_parent, "engine": engine_parent}
    if any(
        type(v) is not str or re.fullmatch(r"sha256:[a-f0-9]{64}", v) is None
        for v in parents.values()
    ):
        raise ValueError("Exact existing local parent image IDs required")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    ids = {}
    for role, dockerfile, argument in TARGETS:
        parent = parents[role]
        tag = "mineagent-local-parent:" + parent.removeprefix("sha256:")
        _docker(["tag", parent, tag], timeout=60)
        _verify_parent_tag(tag, parent)
        iid = output / (role + ".iid")
        _docker(
            [
                "build",
                "--pull=false",
                "--network",
                "none",
                "--iidfile",
                str(iid),
                "-f",
                dockerfile,
                "--build-arg",
                argument + "=" + tag,
                ".",
            ]
        )
        _verify_parent_tag(tag, parent)
        ids[role] = read_iid(iid)
    profile = OEStructuredServerProfile(
        runtime=StructuredRuntimePins(
            controller_image_id=ids["controller"],
            controller_installed_parent_image_id=controller_parent,
            controller_inventory_sha256=HOOK_MANIFEST_SHA256,
            engine_image_id=ids["engine"],
            engine_installed_parent_image_id=engine_parent,
            engine_dependency_lock_sha256=dependency_lock_sha256(),
        )
    )
    verify_images(profile)
    path = output / "oe-structured-runtime-profile.json"
    path.write_text(profile.model_dump_json() + "\n")
    (output / "build-targets.json").write_text(
        json.dumps(
            {role: dockerfile for role, dockerfile, _ in TARGETS}, sort_keys=True
        )
        + "\n"
    )
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--controller-parent", required=True)
    parser.add_argument("--engine-parent", required=True)
    args = parser.parse_args()
    print(
        build(
            args.output,
            controller_parent=args.controller_parent,
            engine_parent=args.engine_parent,
        )
    )


if __name__ == "__main__":
    main()
