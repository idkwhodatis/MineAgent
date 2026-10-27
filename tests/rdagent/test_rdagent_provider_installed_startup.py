"""Wheel-only provider startup, not Docker/provider/PG acceptance."""

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

from runtimes.rdagent.build_profile import wheel
from runtimes.rdagent_operational.build_profile import build

ROOT = Path(__file__).resolve().parents[2]


def test_provider_first_control_request_uses_only_declared_wheel_dependencies(tmp_path):
    output = tmp_path / "wheels"
    output.mkdir()
    proof = json.loads((ROOT / "runtimes/rdagent/wheel-proof.json").read_text())
    files = {
        name: (ROOT / name).read_bytes()
        for name in proof["adapter"]["files"]
        if ".dist-info/" not in name
    }
    base = wheel(output, "mineagent_rdagent_adapter_profile", files, [])
    operational = build(ROOT, output)["operational"]
    installed = tmp_path / "installed"
    installed.mkdir()
    for report in (base, operational):
        with zipfile.ZipFile(output / report["filename"]) as archive:
            archive.extractall(installed)
    # -I + foreign cwd removes checkout imports; fixed package paths also prevent
    # an interpreter's previously installed adapter from concealing missing files.
    code = r"""
import sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root))
import packages, runtimes
packages.__path__ = [str(root / "packages")]
runtimes.__path__ = [str(root / "runtimes")]
from runtimes.rdagent_operational.provider_client import ProviderOperationalHost
from packages.rdagent_provider.contracts import RDModelBinding
from packages.strategy_sdk import parse_json
import packages.strategy_sdk.json as json_parser
assert Path(json_parser.__file__).is_relative_to(root)
class Channel:
    calls = []
    def call(self, method, payload):
        self.calls.append((method, payload))
        return {"current": True}
binding = RDModelBinding(
    profile_id="offline", profile_sha256="a" * 64,
    chat_model="chat", embedding_model="embed", embedding_dimensions=2,
    max_output_tokens=100, request_deadline_ms=8000,
    pricing={"status": "unavailable"},
)
channel = Channel()
ProviderOperationalHost(channel, binding).check_control()
assert channel.calls == [("check_control", {})]
assert parse_json(b'{"bounded":true}') == {"bounded": True}
try:
    parse_json(b'{"duplicate":1,"duplicate":2}')
except ValueError:
    pass
else:
    raise AssertionError("bounded parser was replaced")
print("installed_provider_first_control_ready")
"""
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    command = [sys.executable, "-I", "-c", code, str(installed)]
    result = subprocess.run(
        command,
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "installed_provider_first_control_ready"
    # Original wheel omission deterministically dies before any first RPC.
    (installed / "packages/strategy_sdk").rename(tmp_path / "omitted-sdk")
    missing = subprocess.run(
        command,
        check=False,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert missing.returncode != 0
    assert "No module named 'packages.strategy_sdk'" in missing.stderr
    assert "installed_provider_first_control_ready" not in missing.stdout
