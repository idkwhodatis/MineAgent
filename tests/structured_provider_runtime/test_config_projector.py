"""Compare host Config projector against actual installed pinned upstream, no SDK."""

import json
import os
import subprocess
import sys
from pathlib import Path

from backend.app.oe_structured_config_manifest import expected_config_manifest
from packages.factors.contracts import digest
from tests.openevolve.test_structured_provider_contracts import declared as provider
from tests.openevolve.test_structured_runtime import declared


def test_actual_installed_config_equals_host_without_importing_engine():
    initial, seed = declared()
    binding = provider()[1]
    expected = []
    cases = []
    for slots, tokens, model in ((1, 1, "first/model"), (2, 4096, "second:model")):
        spec = initial.spec.model_copy(update={"maximum_mutation_slots": slots})
        current = binding.model_copy(
            update={"max_output_tokens": tokens, "model": model}
        )
        expected.append(digest(expected_config_manifest(spec, seed, current)))
        cases.append(
            {
                "spec": spec.model_dump(mode="json"),
                "binding": current.model_dump(mode="json"),
                "seed": seed.decode(),
            }
        )
    code = """
import json,socket,sys
from packages.factors.contracts import digest
from packages.openevolve_structured.contracts import StructuredSearchSpec
from packages.openevolve_structured.provider_contracts import OEChatBinding
from packages.openevolve_structured.provider_runtime_config import build_config,config_manifest
def deny(*a,**k): raise AssertionError("network forbidden")
socket.socket.connect=socket.socket.connect_ex=socket.create_connection=deny
results=[]
for value in json.load(sys.stdin):
    spec=StructuredSearchSpec.model_validate_json(json.dumps(value["spec"]))
    binding=OEChatBinding.model_validate_json(json.dumps(value["binding"]))
    results.append(digest(config_manifest(build_config(spec,value["seed"].encode(),binding))))
print(json.dumps(results))
"""
    result = subprocess.run(
        [os.environ.get("MINEAGENT_OE_INSTALLED_PYTHON", sys.executable), "-c", code],
        input=json.dumps(cases),
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
        cwd=Path(__file__).resolve().parents[2],
        env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, result.stderr[-4000:]
    assert json.loads(result.stdout) == expected
