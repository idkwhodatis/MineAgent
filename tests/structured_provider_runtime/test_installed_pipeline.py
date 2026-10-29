"""Actual installed upstream rendering + SDK loopback, not PG/native-engine proof.

Requires existing operator-selected test interpreters; never installs packages.
Missing SDK/upstream is a failure, not a skip or synthesized proposal result.
"""

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import pytest

from packages.factors.contracts import canonical_bytes
from packages.openevolve_structured.provider_contracts import ChatPricing
from packages.openevolve_structured.provider_operator import OEOperatorChatProfile
from packages.rdagent_provider.transport import ProviderFailure, execute
from tests.openevolve.test_structured_provider_contracts import declared
from tests.rdagent_provider.test_transport import binding as rd_binding
from tests.rdagent_provider.test_transport import request as rd_request

ROOT = Path(__file__).resolve().parents[2]
SDK_PYTHON = os.environ.get("MINEAGENT_PROVIDER_SDK_PYTHON", sys.executable)
OE_PYTHON = os.environ.get("MINEAGENT_OE_INSTALLED_PYTHON", sys.executable)
SECRET = "synthetic-oe-localhost-test-only"


@contextmanager
def model_double(parent, *, invalid=False):
    requests = []
    expected = json.loads(parent)
    expected["program"]["threshold"] = 0.07
    generated = canonical_bytes(expected)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, body, self.headers.get("Authorization")))
            # This double computes its candidate response only on receipt of the
            # real SDK request and its actual installed-rendered parent prompt.
            if (
                self.path != "/v1/chat/completions"
                or body.get("response_format") != {"type": "json_object"}
                or parent.decode() not in body["messages"][1]["content"]
            ):
                self.send_error(400)
                return
            proposal = json.loads(parent)
            proposal["program"]["threshold"] = 0.07
            content = canonical_bytes(proposal).decode()
            if invalid:
                content = "```python\nprint('not a closed program')\n```"
            result = {
                "id": "synthetic-observed-oe-request",
                "object": "chat.completion",
                "created": 1,
                "model": "chosen-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": content},
                    }
                ],
                "usage": {
                    "prompt_tokens": 7,
                    "completion_tokens": 11,
                    "total_tokens": 18,
                },
            }
            raw = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests, generated
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def invoke_probe(url, domain, parent):
    owner = uuid4()
    operator = OEOperatorChatProfile(
        connection={
            "id": "normal-oe-test",
            "label": "Synthetic SDK test",
            "model": "chosen-model",
            "base_url": url,
            "secret_env": "IGNORED_TEST_ONLY",
            "owner_ids": (owner,),
            "temperature": 0,
            "max_output_tokens": 4096,
            "request_timeout_seconds": 8,
            "max_request_bytes": 262144,
            "max_response_bytes": 262144,
            "synthetic": True,
        },
        pricing=ChatPricing(),
    )
    payload = {
        "owner_id": str(owner),
        "run_id": str(uuid4()),
        "epoch_id": str(uuid4()),
        "intent_id": str(uuid4()),
        "parent_id": str(uuid4()),
        "binding": operator.public_binding(owner).model_dump(mode="json"),
        "domain": domain.model_dump(mode="json"),
        "parent": parent.decode(),
        "base_url": url,
        "secret": SECRET,
        "sdk_python": SDK_PYTHON,
    }
    # Private pipe and sanitized environment; credentials never enter argv/logs.
    result = subprocess.run(
        [OE_PYTHON, "-I", str(ROOT / "tests/openevolve/structured_provider_probe.py")],
        check=False,
        input=json.dumps(payload).encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=15,
        env={"PATH": os.defpath, "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert len(result.stdout) < 262144 and SECRET.encode() not in result.stdout
    return result.returncode, json.loads(result.stdout)


def test_installed_prompt_real_sdk_generated_json_child():
    _, _, domain, _, parent, *_ = declared()
    with model_double(parent) as (url, requests, generated):
        code, observed = invoke_probe(url, domain, parent)
    assert code == 0, observed
    assert len(requests) == 1 and requests[0][2] == "Bearer " + SECRET
    assert observed["request_schema"] == "mineagent.oe-structured-provider-chat.v1"
    assert observed["candidate"].encode() == generated
    assert observed["candidate_sha256"] == hashlib.sha256(generated).hexdigest()
    assert observed["candidate_sha256"] != observed["parent_candidate_sha256"]
    assert observed["parent_candidate_sha256"] == hashlib.sha256(parent).hexdigest()
    assert (
        observed["input_tokens"],
        observed["output_tokens"],
        observed["total_tokens"],
    ) == (7, 11, 18)
    assert (
        observed["usage_source"] == "synthetic_provider"
        and observed["billing_status"] == "unknown"
    )
    assert observed["physical_group_stopped"] and observed["sdk_start_ticks"] > 0
    with pytest.raises(ProcessLookupError):
        os.killpg(observed["sdk_pid"], 0)
    assert observed["engine_units"] == 0 and observed["supplied_seed_metrics"] is False
    assert (
        observed["qualification"]
        == "installed_sampler_sdk_localhost_non_pg_no_engine_authority"
    )


def test_actual_provider_python_response_cannot_become_json_candidate():
    _, _, domain, _, parent, *_ = declared()
    with model_double(parent, invalid=True) as (url, requests, _):
        code, observed = invoke_probe(url, domain, parent)
    assert code == 1 and len(requests) == 1
    assert observed == {"error": "structured_provider_probe_failed"}


def test_old_rd_dispatch_does_not_accept_oe_request_without_explicit_protocol(
    monkeypatch,
):
    # Match model-binding identity from one case for the wrong protocol test.
    _, binding, _, _, _, _, _, request = declared()
    monkeypatch.setattr(
        subprocess, "Popen", lambda *args, **kwargs: pytest.fail("must not launch")
    )
    with pytest.raises(ProviderFailure) as error:
        execute(
            request,
            binding=binding,
            base_url="https://example.invalid/v1",
            secret=SECRET,
            deadline_monotonic=time.monotonic() + 5,
        )
    assert (
        error.value.code == "provider_input_rejected"
        and not error.value.outcome_unknown
    )


def test_oe_dispatch_does_not_relabel_rd_request(monkeypatch):
    monkeypatch.setattr(
        subprocess, "Popen", lambda *args, **kwargs: pytest.fail("must not launch")
    )
    with pytest.raises(ProviderFailure) as error:
        execute(
            rd_request(),
            binding=rd_binding(),
            base_url="https://example.invalid/v1",
            secret=SECRET,
            deadline_monotonic=time.monotonic() + 5,
            protocol="mineagent.oe-structured-provider-chat.v1",
        )
    assert (
        error.value.code == "provider_input_rejected"
        and not error.value.outcome_unknown
    )


def test_fixture_image_does_not_accidentally_copy_host_provider_successors():
    text = (ROOT / "runtimes/openevolve_structured/Dockerfile.dockerignore").read_text()
    lines = text.splitlines()
    assert lines.index("packages/openevolve_structured/provider_*.py") > lines.index(
        "!packages/openevolve_structured/*.py"
    )
    assert not any(
        line.startswith("!packages/openevolve_structured/provider_") for line in lines
    )
