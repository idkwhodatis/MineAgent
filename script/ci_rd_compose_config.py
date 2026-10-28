"""Hosted-only private synthetic operator settings from actual previously built IDs."""

import json
import os
import sys
from pathlib import Path
from uuid import UUID

from packages.rdagent_adapter.controller_profile import COMPOSED_CONTROLLER_PROFILE
from packages.rdagent_provider.contracts import DeclaredTokenPricing
from packages.rdagent_provider.operator_profiles import RDOperatorModelProfile
from script.build_rd_compose_images import profile_from_ids, read_iid


def write_config(owner_text):
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise ValueError("Disposable hosted job required")
    owner = UUID(owner_text)
    if str(owner) != owner_text:
        raise ValueError("Exact owned fixture identity required")
    evidence = Path("var/numeric-proof")
    runtime = profile_from_ids(
        {
            name: read_iid(evidence / (name + ".iid"))
            for name in (
                "orchestrator",
                "candidate",
                "normalizer",
                "analysis",
                "qlib-installed",
            )
        }
    )
    # This new gate exercises the full composed SDK/factor/analysis lifecycle.
    # Keep profile_from_ids default and all historical declarations unchanged.
    runtime = type(runtime).model_validate(
        {
            **runtime.model_dump(mode="json"),
            "controller_profile": COMPOSED_CONTROLLER_PROFILE,
        }
    )
    operator = RDOperatorModelProfile(
        connection={
            "id": "compose-local-sdk",
            "label": "Synthetic in-worker HTTP fixture",
            "model": "local-chat",
            "base_url": "http://127.0.0.1:18765/v1",
            "secret_env": "MINEAGENT_LOCAL_PROVIDER_TEST_SECRET",
            "owner_ids": (owner,),
            "temperature": 0,
            "max_output_tokens": 4096,
            "request_timeout_seconds": 8,
            "max_request_bytes": 262144,
            "max_response_bytes": 262144,
            "synthetic": True,
        },
        embedding_model="local-embedding",
        embedding_dimensions=2,
        pricing=DeclaredTokenPricing(
            chat_input_microusd_per_million=100,
            chat_output_microusd_per_million=100,
            embedding_microusd_per_million=100,
        ),
    )
    path = Path(os.environ["TRADEGRAD_STATE_ROOT"]) / "provider.env"
    values = {
        "RDAGENT_RESEARCH_ENABLED": "true",
        "RDAGENT_SYNTHETIC_PROVIDER_ENABLED": "true",
        "RDAGENT_LIVE_PROVIDER_ENABLED": "false",
        "RDAGENT_PROVIDER_RUNTIME_PROFILE": runtime.model_dump_json(),
        "RDAGENT_PROVIDER_PROFILES": json.dumps(
            [operator.model_dump(mode="json")], separators=(",", ":")
        ),
        "MINEAGENT_LOCAL_PROVIDER_TEST_SECRET": "synthetic-local-only",
    }
    with path.open("x", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        for key, value in values.items():
            if "\n" in value or "\r" in value or "$" in value:
                raise ValueError(
                    "Single-line noninterpolated fixture settings required"
                )
            stream.write(key + "=" + value + "\n")
    return path


if __name__ == "__main__":
    try:
        write_config(sys.argv[1])
    except Exception:  # noqa: BLE001 - private operator declaration never in diagnostics
        raise SystemExit("Synthetic packaged Compose configuration failed") from None
