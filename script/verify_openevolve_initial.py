"""Strict required actual-epoch evidence, plus confined installed import probe.

No result fixture is generated here. Raw JUnit and database/storage artifacts stay
private; only normalized outcomes and bounded assertions enter the public proof.
"""

import argparse
import importlib
import json
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

CASES = {
    "test_actual_initial_epoch_four_saves_and_cold_restore",
    "test_actual_initial_owner_loss_unread_original_deadline",
}


def installed():
    import os
    import socket
    import subprocess

    assert sys.flags.isolated and os.getresuid() == (65533,) * 3
    attempts = 0

    def forbidden(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise AssertionError("installed import attempted network connection")

    socket.socket.connect = forbidden
    socket.socket.connect_ex = forbidden
    socket.create_connection = forbidden
    names = (
        "openevolve.api",
        "packages.openevolve_adapter.observer",
        "packages.openevolve_adapter.observer_transport",
        "packages.openevolve_adapter.placement_observer",
        "packages.openevolve_adapter.runtime_config",
        "runtimes.openevolve_operational.entrypoint",
        "runtimes.openevolve_operational.evaluator",
        "runtimes.openevolve_operational.transport",
    )
    modules = {}
    for name in names:
        module = importlib.import_module(name)
        path = str(Path(module.__file__).resolve())
        assert path.startswith("/usr/local/lib/python3.12/site-packages/")
        modules[name] = path
    from packages.openevolve_adapter import observer_transport
    from runtimes.openevolve_operational import transport

    assert observer_transport.host_event is transport.host_event
    subprocess.run(
        [sys.executable, "-I", "-m", "pip", "check"],
        check=True,
        capture_output=True,
        timeout=30,
    )
    assert attempts == 0
    print(
        json.dumps(
            {
                "isolated_python": True,
                "network_attempts": attempts,
                "installed_modules": modules,
                "pip_check": True,
            },
            sort_keys=True,
        )
    )


def verify(junit, proof):
    # Script execution does not implicitly include checkout root in sys.path.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from runtimes.openevolve_operational.recipe_pins import PINS
    from script.summarize_ci_junit import summarize

    summarize(junit, proof / "outcomes.xml")
    cases = list(ET.parse(junit).getroot().iter("testcase"))
    assert len(cases) == 2 and {case.get("name") for case in cases} == CASES
    assert not any(
        case.find(tag) is not None
        for case in cases
        for tag in ("failure", "error", "skipped")
    )
    assert json.loads((proof / "recipe-pins.json").read_text()) == {
        key: value[1] for key, value in PINS.items()
    }
    cold = json.loads((proof / "initial-cold.json").read_text())
    expected = {
        "candidates": 3,
        "model_calls": 2,
        "native_evaluations": 3,
        "checkpoint_save_ordinals": [1, 2, 3, 4],
        "checkpoint_frontiers": [0, 1, 2, 2],
        "all_processes_stopped": True,
        "schema_revision": "024_openevolve_initial",
        "actual_exit_code": 124,
        "cold_all_closures_equal": True,
        "cold_all_files_equal": True,
        "production_entrypoint": False,
    }
    assert cold == expected
    loss = json.loads((proof / "owner-loss.json").read_text())
    assert loss == {
        "original_deadline_seconds": 600,
        "actual_exit_code": 124,
        "kernel_absent_before_cleanup": True,
        "no_replacement_reader": True,
        "reservation_preserved": True,
        "successful_run": False,
        "persistent_recovery_proved": True,
    }
    probe = json.loads((proof / "installed.json").read_text())
    assert probe["isolated_python"] is True and probe["pip_check"] is True
    assert type(probe["network_attempts"]) is int and probe["network_attempts"] == 0
    assert len(probe["installed_modules"]) == 8
    for role in ("controller", "native", "parent"):
        assert re.fullmatch(
            r"sha256:[0-9a-f]{64}", (proof / (role + ".iid")).read_text().strip()
        )
    for name in ("native-source.sha256", "wrapper.sha256"):
        assert re.fullmatch(r"[0-9a-f]{64}", (proof / name).read_text().strip())
    print(
        "Two exact actual cases, cold closure and autonomous recovery evidence verified"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--installed", action="store_true")
    parser.add_argument("--junit", type=Path)
    parser.add_argument("--proof", type=Path)
    args = parser.parse_args()
    if args.installed:
        assert args.junit is None and args.proof is None
        installed()
    else:
        assert args.junit is not None and args.proof is not None
        verify(args.junit, args.proof)


if __name__ == "__main__":
    main()
