"""Fixed real installed initial-epoch run_evolution; no mocks/provider fallback."""

import multiprocessing
import os
import sys
from pathlib import Path


def main():
    if (
        not sys.flags.isolated
        or len(sys.argv) != 1
        or os.getresuid() != (65533,) * 3
        or os.getresgid() != (65533,) * 3
    ):
        raise RuntimeError("fixed OE controller identity")
    if set(os.listdir("/bootstrap")) != {"bootstrap.json", "seed.json"} or set(
        os.listdir("/ipc")
    ) != {"ticket.json", "events.sock", "broker.sock"}:
        raise RuntimeError("closed controller input layout")
    from packages.openevolve_structured import observer
    from packages.openevolve_structured import observer_transport as transport
    from runtimes.openevolve_operational.fixed_input import read_fixed

    bootstrap = transport._bootstrap()
    from runtimes.openevolve_structured.verify_hooks import verify_installed_hooks

    verify_installed_hooks(bootstrap.adapter_patch_sha256)
    seed = read_fixed("/bootstrap/seed.json", 32768)
    if bootstrap.schema_version in {
        "mineagent.oe-structured-bootstrap.v2",
        "mineagent.oe-structured-bootstrap.v3",
    }:
        from packages.openevolve_structured.provider_runtime_config import build_config

        config = build_config(bootstrap.spec, seed, bootstrap.model_binding)
    else:
        from packages.openevolve_structured.runtime_config import build_config

        config = build_config(bootstrap.spec, seed)
    multiprocessing.set_start_method("spawn")
    Path("/work/checkpoints").mkdir(mode=0o700)
    from openevolve.api import run_evolution

    with observer.epoch_control(bootstrap.epoch_id):
        run_evolution(
            "/bootstrap/seed.json",
            str(Path(__file__).with_name("evaluator.py")),
            config=config,
            iterations=bootstrap.spec.maximum_mutation_slots,
            output_dir="/work/evolution",
            cleanup=False,
        )
    # Actual final snapshot/stop/output accounting is host responsibility. A
    # process exit alone is never a run-success receipt.


if __name__ == "__main__":
    main()
