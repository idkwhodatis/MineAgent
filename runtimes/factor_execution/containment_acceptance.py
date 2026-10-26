"""Actual fixed numeric containment cases; no production admission or host native parsing."""

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

from runtimes.factor_execution.acceptance import exercise, frozen_inputs
from runtimes.factor_execution.containment_fixture import MARKER, MODES
from runtimes.factor_execution.runtime_contract import BASE_IMAGE, image_inventory
from runtimes.factor_execution.verify_inventory import verify
from runtimes.hostile_deadline.acceptance import command, require_local_docker


def fixture_source(mode):
    if type(mode) is not str or mode not in MODES:
        raise ValueError("closed maintained containment source required")
    return (
        b"MODE = "
        + repr(mode).encode("ascii")
        + b"\n"
        + Path(__file__).with_name("containment_fixture.py").read_bytes()
    )


def require_report(proof):
    if (
        proof.get("scope") != "disabled_maintained_numeric_containment_only"
        or proof.get("arbitrary_candidate_admission") is not False
    ):
        raise ValueError("incorrect numeric containment scope")
    cases = proof.get("cases", [])
    if [item.get("observation_mode") for item in cases] != list(MODES):
        raise ValueError("missing/repeated containment cases")
    for unit in cases:
        mode = unit["observation_mode"]
        if unit["role"] != "candidate" or not all(
            unit[key] is True
            for key in (
                "positive_stop_verified",
                "original_deadline_verified",
                "launch_owner_killed",
                "all_observed_descendants_stopped",
                "host_sibling_survived",
            )
        ):
            raise ValueError("missing actual containment observations")
        if unit["exit_code"] != 124 or not 0 < unit["elapsed_seconds"] <= 9:
            raise ValueError("stop exceeded original numeric deadline")
        if unit["four_live_candidate_processes_observed"] is not (
            mode in ("tree_quiescence", "tree_deadline")
        ):
            raise ValueError("wrong observed live native tree")
        if unit["live_protected_exporter_observed_twice"] is not (
            mode in ("no_reader", "collector_disconnected")
        ):
            raise ValueError("missing unread live numeric exporter")
        if unit["kernel_exit_before_reader_close_verified"] is not (
            mode == "no_reader"
        ):
            raise ValueError("kernel-before-close ordering not established")
        if unit["primary_collector_disconnected"] is not (
            mode == "collector_disconnected"
        ):
            raise ValueError("collector loss not established")
        if mode == "tree_quiescence":
            if (
                unit["output_sha256"] != hashlib.sha256(MARKER).hexdigest()
                or unit["output_bytes"] != len(MARKER)
                or unit["observed_child_exit_code"] != 0
                or not all(
                    unit[key] is True
                    for key in (
                        "complete_output_stream_verified",
                        "frame_received_while_unit_alive",
                        "protected_quiescence_frame_verified",
                    )
                )
            ):
                raise ValueError("fixed native probes did not finish before quiescence")
        elif (
            unit["output_bytes"] != 0
            or unit["observed_child_exit_code"] is not None
            or unit["frame_received_while_unit_alive"] is not False
            or unit["protected_quiescence_frame_verified"] is not False
        ):
            raise ValueError("unobserved output cannot be promoted")
        if unit["complete_output_stream_verified"] is not (
            mode in ("tree_quiescence", "tree_deadline")
        ):
            raise ValueError("false output stream completeness")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run-maintained-containment-docker-fixtures",
        action="store_true",
        required=True,
    )
    parser.add_argument("--candidate-image", required=True)
    parser.add_argument("--normalizer-image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sources = verify()
    require_local_docker()
    base = json.loads(command("docker", "image", "inspect", BASE_IMAGE))[0]
    inventory = image_inventory(
        base,
        json.loads(command("docker", "image", "inspect", args.candidate_image))[0],
        role="candidate",
    )
    proof = {
        "scope": "disabled_maintained_numeric_containment_only",
        "arbitrary_candidate_admission": False,
        "runtime_inventory": sources,
        "image_inventory": inventory,
        "cases": [],
    }
    with tempfile.TemporaryDirectory(
        prefix="mineagent-numeric-containment-"
    ) as directory:
        folder = Path(directory)
        folder.chmod(0o755)
        for mode in MODES:
            print("numeric containment case: " + mode, flush=True)
            _, inputs, _ = frozen_inputs(
                args.candidate_image,
                args.normalizer_image,
                source_bytes=fixture_source(mode),
            )
            _, evidence = exercise(
                args.candidate_image,
                folder / mode,
                role="candidate",
                inventory=inventory,
                input_bytes=inputs,
                observation_mode=mode,
            )
            proof["cases"].append(evidence)
    require_report(proof)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
