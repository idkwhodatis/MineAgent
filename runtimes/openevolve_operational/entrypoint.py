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
    if set(os.listdir("/bootstrap")) != {"bootstrap.json", "seed.py"} or set(
        os.listdir("/ipc")
    ) != {"ticket.json", "events.sock", "broker.sock"}:
        raise RuntimeError("closed controller input layout")
    from packages.openevolve_adapter import (
        observer,
        observer_transport,
        placement_observer,
    )
    from packages.openevolve_adapter.runtime_config import build_config
    from runtimes.openevolve_operational import transport
    from runtimes.openevolve_operational.fixed_input import read_fixed

    # A phase6 image must not accidentally pass by using the old transport.
    if observer_transport.host_event is not transport.host_event:
        raise RuntimeError("operational adapter inventory missing")
    bootstrap = transport._bootstrap()
    seed = read_fixed("/bootstrap/seed.py", 32768)
    config = build_config(bootstrap.spec, seed)
    multiprocessing.set_start_method("spawn")
    placement_observer.send_placement = transport.send_placement
    Path("/work/checkpoints").mkdir(mode=0o700)
    from openevolve.api import run_evolution

    with observer.epoch_control(bootstrap.epoch_id):
        run_evolution(
            "/bootstrap/seed.py",
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
