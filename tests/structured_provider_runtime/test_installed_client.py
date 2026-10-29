"""Real installed upstream Config/client, bounded host-reply doubles; no engine/PG."""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from packages.openevolve_structured.contracts import StructuredSearchSpec
from packages.openevolve_structured.provider_bootstrap import (
    ProviderStructuredBootstrap,
)
from tests.openevolve.test_structured_objective import profile as objective
from tests.openevolve.test_structured_provider_contracts import declared


@pytest.mark.parametrize(
    "mode",
    [
        "valid",
        "unknown_usage",
        "replay",
        "concurrent_tasks",
        "concurrent_threads",
        "artifact_storage",
        "changed_prompt",
        "truncated",
        "legacy_reply",
        "wrong_context",
        "wrong_admission",
    ],
)
def test_installed_normal_factory_and_one_use_bound_client(mode):
    _, binding, domain, _, parent, _, context, request = declared()
    spec = StructuredSearchSpec(
        owner_id=context.owner_id,
        run_id=context.run_id,
        execution_binding_id=uuid4(),
        execution_binding_sha256=context.execution_binding_sha256,
        domain=domain,
        seed_candidate_sha256=context.parent_candidate_sha256,
        objective=objective(),
        maximum_mutation_slots=1,
    )
    now = datetime.now(UTC)
    initial = ProviderStructuredBootstrap(
        admission_id=uuid4(),
        epoch_id=context.epoch_id,
        spec=spec,
        issued_at=now,
        not_after=now + timedelta(seconds=600),
        physical_profile_sha256="1" * 64,
        runtime_profile_sha256="2" * 64,
        config_sha256="3" * 64,
        adapter_patch_sha256="4" * 64,
        model_binding=binding,
        model_binding_sha256=binding.sha256,
    )
    root = Path(__file__).resolve().parents[2]
    python = os.environ.get("MINEAGENT_OE_INSTALLED_PYTHON", sys.executable)
    result = subprocess.run(
        [python, str(root / "tests/openevolve/structured_provider_client_probe.py")],
        input=json.dumps(
            {
                "mode": mode,
                "bootstrap": initial.model_dump(mode="json"),
                "request": request.model_dump(mode="json"),
                "context": context.model_dump(mode="json"),
                "parent": parent.decode(),
            }
        ),
        check=False,
        text=True,
        capture_output=True,
        timeout=20,
        env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, result.stderr[-2000:]
    report = json.loads(result.stdout)
    assert report["engine_units"] == 0
    if mode == "artifact_storage":
        assert report["before_none"] is True and report["retained_exact"] is True
        assert report["calls"] == 0
    elif mode in {"valid", "unknown_usage"}:
        assert report["generated"] is True and report["calls"] == 1
    else:
        assert report["rejected"] in {"BrokerAbort", "CandidateSourceRejected"}
