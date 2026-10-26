"""Closed actual-Docker native rejection matrix; no host HDF parser/activation."""

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

from runtimes.factor_execution.acceptance import exercise, frozen_inputs
from runtimes.factor_execution.adverse_fixture import CASES
from runtimes.factor_execution.runtime_contract import BASE_IMAGE, image_inventory
from runtimes.factor_execution.verify_inventory import verify
from runtimes.hostile_deadline.acceptance import command, require_local_docker

OUTPUT_REJECTIONS = {
    "fifo": 0,
    "symlink": 0,
    "unreadable": 0,
    "hardlink": 0,
    "file_limit": 42,
}


def fixture_source(case):
    if type(case) is not str or case not in CASES:
        raise ValueError("closed maintained adverse source required")
    source = Path(__file__).with_name("adverse_fixture.py").read_bytes()
    return b"CASE = " + repr(case).encode("ascii") + b"\n" + source


def normalization_inputs(hdf, listings, event_ns):
    if not isinstance(hdf, bytes) or not 0 < len(hdf) <= 16 * 1024 * 1024:
        raise ValueError("opaque fixture bounds")
    manifest = {
        "schema_version": "mineagent.factor-normalization.v1",
        "hdf_sha256": hashlib.sha256(hdf).hexdigest(),
        "listings_sha256": hashlib.sha256(listings).hexdigest(),
        "output_event_ns": event_ns,
        "max_rows": 3,
        "max_bytes": 16 * 1024 * 1024,
    }
    return {
        "/input/result.h5": hdf,
        "/input/listings.json": listings,
        "/input/manifest.json": json.dumps(
            manifest, sort_keys=True, separators=(",", ":")
        ).encode(),
    }


def require_report(proof):
    if (
        proof.get("scope") != "disabled_maintained_numeric_adverse_only"
        or proof.get("arbitrary_candidate_admission") is not False
        or len(proof.get("cases", [])) != len(CASES)
    ):
        raise ValueError("incomplete numeric adverse proof")
    if [item["name"] for item in proof["cases"]] != list(CASES):
        raise ValueError("missing/reordered/duplicate adverse cases")
    for item in proof["cases"]:
        expected_roles = (
            ["candidate"]
            if item["name"] in OUTPUT_REJECTIONS
            else ["candidate", "normalizer"]
        )
        if [unit["role"] for unit in item["units"]] != expected_roles:
            raise ValueError("missing numeric unit")
        for unit in item["units"]:
            expected_exit = (
                OUTPUT_REJECTIONS.get(item["name"])
                if unit["role"] == "candidate"
                else 2
            )
            observed = unit.get("observed_child_exit_code")
            if type(observed) is not int or observed != (
                0 if expected_exit is None else expected_exit
            ):
                raise ValueError("wrong observed protected child outcome")
            if unit["expected_rejection_exit_code"] != expected_exit:
                raise ValueError("wrong protected child outcome")
            if not all(
                unit[key] is True
                for key in (
                    "positive_stop_verified",
                    "original_deadline_verified",
                    "complete_output_stream_verified",
                    "frame_received_while_unit_alive",
                    "launch_owner_killed",
                    "all_observed_descendants_stopped",
                    "protected_quiescence_frame_verified",
                )
            ):
                raise ValueError("missing live/stop proof")
            if unit["exit_code"] != 124 or not 0 < unit["elapsed_seconds"] <= 9:
                raise ValueError("numeric deadline drift")
            if expected_exit is not None and unit["output_bytes"] != 0:
                raise ValueError("rejected output cannot be published")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run-maintained-adverse-docker-fixtures", action="store_true", required=True
    )
    parser.add_argument("--candidate-image", required=True)
    parser.add_argument("--normalizer-image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inventory = verify()
    require_local_docker()
    base = json.loads(command("docker", "image", "inspect", BASE_IMAGE))[0]
    images = {"candidate": args.candidate_image, "normalizer": args.normalizer_image}
    inventories = {
        role: image_inventory(
            base, json.loads(command("docker", "image", "inspect", image))[0], role=role
        )
        for role, image in images.items()
    }
    proof = {
        "scope": "disabled_maintained_numeric_adverse_only",
        "arbitrary_candidate_admission": False,
        "runtime_inventory": inventory,
        "image_inventories": inventories,
        "cases": [],
    }
    with tempfile.TemporaryDirectory(prefix="mineagent-numeric-adverse-") as directory:
        folder = Path(directory)
        folder.chmod(0o755)
        for case in CASES:
            print("numeric adverse case: " + case, flush=True)
            _, inputs, event_ns = frozen_inputs(
                args.candidate_image,
                args.normalizer_image,
                source_bytes=fixture_source(case),
            )
            hdf, candidate = exercise(
                args.candidate_image,
                folder / (case + "-candidate"),
                role="candidate",
                inventory=inventories["candidate"],
                input_bytes=inputs,
                rejected_exit_code=OUTPUT_REJECTIONS.get(case),
            )
            units = [candidate]
            if case not in OUTPUT_REJECTIONS:
                _, normalizer = exercise(
                    args.normalizer_image,
                    folder / (case + "-normalizer"),
                    role="normalizer",
                    inventory=inventories["normalizer"],
                    input_bytes=normalization_inputs(
                        hdf, inputs["/input/listings.json"], event_ns
                    ),
                    rejected_exit_code=2,
                )
                units.append(normalizer)
            proof["cases"].append({"name": case, "units": units})
    require_report(proof)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
