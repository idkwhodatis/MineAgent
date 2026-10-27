"""Deterministic additional operational wheel; compatibility wheels stay unchanged."""

import argparse
import hashlib
import json
from pathlib import Path

from runtimes.rdagent.build_profile import wheel

FILES = (
    "runtimes/rdagent_operational/rpc.py",
    "runtimes/rdagent_operational/client.py",
    "runtimes/rdagent_operational/adapter.py",
    "runtimes/rdagent_operational/entrypoint.py",
    "runtimes/rdagent_operational/deadline.py",
    "runtimes/rdagent_operational/trusted_report.py",
    "packages/rdagent_adapter/operational_bootstrap.py",
    "packages/rdagent_adapter/seed_parent.py",
    "packages/rdagent_adapter/seed_prompt_budget.py",
    "packages/rdagent_adapter/seeded_bootstrap.py",
    "runtimes/rdagent_operational/seeded_scenario.py",
    "packages/rdagent_adapter/steer.py",
    "packages/rdagent_adapter/provider_config.py",
    "packages/rdagent_adapter/provider_bootstrap.py",
    "packages/rdagent_adapter/provider_prompt.py",
    "packages/rdagent_provider/__init__.py",
    "packages/rdagent_provider/contracts.py",
    "packages/rdagent_provider/codec.py",
    # Provider codec imports the portable bounded JSON parser through this package.
    # Preserve its real import closure; no candidate source or engine is bundled.
    "packages/strategy_sdk/__init__.py",
    "packages/strategy_sdk/base.py",
    "packages/strategy_sdk/contracts.py",
    "packages/strategy_sdk/json.py",
    "packages/strategy_sdk/state.py",
    "packages/strategy_sdk/views.py",
    "runtimes/rdagent_operational/provider_client.py",
    "runtimes/rdagent_operational/provider_scenario.py",
    "packages/research_runtime/engine_broker_contracts.py",
    "packages/research_runtime/broker_contracts.py",
    "runtimes/hostile_deadline/deadline.py",
    "runtimes/hostile_deadline/contract.py",
)


def build(root, output):
    root = Path(root).resolve(strict=True)
    files = {}
    for relative in FILES:
        path = root / relative
        if path.resolve(strict=True) != path or not path.is_file():
            raise ValueError("Only exact regular source inventory is allowed")
        files[relative] = path.read_bytes()
    output.mkdir(parents=True, exist_ok=True)
    result = wheel(
        output,
        "mineagent_rdagent_operational_profile",
        files,
        ["mineagent-rdagent-adapter-profile==0.1.0"],
    )
    report = {
        "schema_version": "mineagent.rdagent-operational-wheel.v1",
        "operational": result,
        "files": {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()},
        "recipe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "admission": "closed_until_actual_composed_acceptance",
    }
    (output / "operational-build-report.json").write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n"
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(Path(__file__).resolve().parents[2], args.output)
