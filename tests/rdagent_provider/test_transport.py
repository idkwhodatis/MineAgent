"""Real SDK routes against a local provider double; never paid-provider evidence."""

import copy
import json
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from packages.rdagent_provider.contracts import (
    PricingUnavailable,
    RDModelBinding,
    RDProviderChatRequest,
    RDProviderEmbeddingRequest,
)
from packages.rdagent_provider.sdk_process import (
    RejectedResponse,
    strict_json,
    validate_document,
)
from packages.rdagent_provider.transport import (
    ProviderFailure,
    _stop_group,
    _wire_exchange,
    execute,
)

SECRET = "synthetic-test-credential-not-real"
SDK_PYTHON = os.environ.get("MINEAGENT_PROVIDER_SDK_PYTHON", sys.executable)


def binding():
    return RDModelBinding(
        profile_id="test",
        profile_sha256="a" * 64,
        chat_model="chat-test",
        embedding_model="embed-test",
        embedding_dimensions=2,
        max_output_tokens=100,
        request_deadline_ms=8000,
        pricing=PricingUnavailable(),
        synthetic=True,
    )


def request(kind="chat", *, json_object=False):
    fixed = binding()
    shared = {
        "model_profile_id": fixed.profile_id,
        "model_binding_sha256": fixed.sha256,
    }
    if kind == "chat":
        return RDProviderChatRequest(
            **shared,
            model=fixed.chat_model,
            messages=({"role": "user", "content": "hello"},),
            max_output_tokens=100,
            response_format={"type": "json_object"} if json_object else None,
        )
    return RDProviderEmbeddingRequest(
        **shared, model=fixed.embedding_model, inputs=("one", "two"), dimensions=2
    )


def document(kind="chat"):
    if kind == "chat":
        return {
            "id": "chat-synthetic",
            "object": "chat.completion",
            "created": 1,
            "model": "chat-test",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": '{"ok":true}'},
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
    return {
        "object": "list",
        "model": "embed-test",
        "data": [
            {"index": 0, "object": "embedding", "embedding": [0.25, -0.5]},
            {"index": 1, "object": "embedding", "embedding": [1.0, 0.0]},
        ],
        "usage": {"prompt_tokens": 4, "total_tokens": 4},
    }


@contextmanager
def provider(raw, *, status=200, delay=0, headers=None):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            calls.append(
                (
                    self.path,
                    json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                    self.headers.get("Authorization"),
                )
            )
            time.sleep(delay)
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def run(req, url, events, *, timeout=8):
    return execute(
        req,
        binding=binding(),
        base_url=url,
        secret=SECRET,
        deadline_monotonic=time.monotonic() + timeout,
        boundary=events.append,
        python_executable=SDK_PYTHON,
    )


@pytest.mark.parametrize("kind", ["chat", "embedding"])
def test_actual_sdk_route_and_usage(kind):
    events = []
    with provider(json.dumps(document(kind)).encode()) as (url, calls):
        result = run(request(kind, json_object=kind == "chat"), url, events)
    assert len(calls) == 1
    assert calls[0][0] == (
        "/v1/chat/completions" if kind == "chat" else "/v1/embeddings"
    )
    assert calls[0][2] == "Bearer " + SECRET
    assert events == ["admitted", "stopped"]
    assert result.contained and result.billing_status == "unknown"
    assert result.usage_source == "synthetic_provider"
    if kind == "chat":
        assert result.content == '{"ok":true}' and result.finish_reason == "stop"
        assert (result.input_tokens, result.output_tokens, result.total_tokens) == (
            3,
            2,
            5,
        )
        assert calls[0][1]["response_format"] == {"type": "json_object"}
    else:
        assert result.vectors == ((0.25, -0.5), (1.0, 0.0))
        assert result.output_tokens is None
        assert calls[0][1]["encoding_format"] == "float"


@pytest.mark.parametrize(
    "fault", ["duplicate", "partial", "http500", "encoded", "too_large"]
)
def test_actual_sdk_bad_response_never_retries_or_refunds(fault):
    raw, status, headers = json.dumps(document()).encode(), 200, None
    if fault == "duplicate":
        raw = raw.replace(b'"created": 1', b'"created":1,"created":2')
    elif fault == "partial":
        raw = raw[:50]
    elif fault == "http500":
        status = 500
    elif fault == "encoded":
        headers = {"Content-Encoding": "gzip"}
    else:
        raw = b"x" * 262145
    events = []
    with (
        provider(raw, status=status, headers=headers) as (url, calls),
        pytest.raises(ProviderFailure) as error,
    ):
        run(request(), url, events)
    assert len(calls) == 1 and events == ["admitted", "stopped"]
    assert error.value.outcome_unknown and error.value.contained
    assert SECRET not in str(error.value)


def test_actual_sdk_slow_response_total_wall_and_positive_stop():
    events = []
    with provider(json.dumps(document()).encode(), delay=3) as (url, calls):
        started = time.monotonic()
        with pytest.raises(ProviderFailure) as error:
            run(request(), url, events, timeout=1.5)
        elapsed = time.monotonic() - started
    assert len(calls) == 1 and elapsed < 2.5
    assert error.value.outcome_unknown and error.value.contained
    assert events == ["admitted", "stopped"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda x: x["usage"].update(prompt_tokens=True),
        lambda x: x["usage"].update(total_tokens=6),
        lambda x: x["choices"][0].update(index=True),
        lambda x: x["choices"][0].update(finish_reason="tool_calls"),
        lambda x: x["choices"].append(copy.deepcopy(x["choices"][0])),
        lambda x: x["choices"][0]["message"].update(tool_calls=[{}]),
        lambda x: x["choices"][0]["message"].update(content='{"x":1,"x":2}'),
        lambda x: x["choices"][0]["message"].update(content='{"x":NaN}'),
    ],
)
def test_strict_chat_primitives(mutation):
    value = document()
    mutation(value)
    with pytest.raises((RejectedResponse, ValueError)):
        validate_document(
            value, request(json_object=True).model_dump(mode="json"), True
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda x: x["data"][1].update(index=0),
        lambda x: x["data"][0].update(embedding=[True, 0]),
        lambda x: x["data"][0].update(embedding=[float("inf"), 0]),
        lambda x: x["data"].pop(),
        lambda x: x["data"][0].update(embedding=[0]),
        lambda x: x["usage"].update(completion_tokens=0),
    ],
)
def test_strict_embedding_primitives(mutation):
    value = document("embedding")
    mutation(value)
    with pytest.raises(RejectedResponse):
        validate_document(value, request("embedding").model_dump(mode="json"), False)


def test_missing_usage_is_unknown_not_zero_and_length_remains_explicit():
    value = document()
    del value["usage"]
    value["choices"][0].update(finish_reason="length")
    value["choices"][0]["message"]["content"] = '{"unfinished":'
    result = validate_document(
        value, request(json_object=True).model_dump(mode="json"), False
    )
    assert (
        result["input_tokens"]
        is result["output_tokens"]
        is result["total_tokens"]
        is None
    )
    assert result["billing_status"] == "unknown" and result["finish_reason"] == "length"


def test_expired_or_bad_binding_cannot_launch(monkeypatch):
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **k: pytest.fail("must not launch")
    )
    for req, deadline in (
        (request(), time.monotonic() - 1),
        (
            request().model_copy(update={"max_output_tokens": True}),
            time.monotonic() + 5,
        ),
    ):
        with pytest.raises(ProviderFailure) as error:
            execute(
                req,
                binding=binding(),
                base_url="https://example.invalid/v1",
                secret=SECRET,
                deadline_monotonic=deadline,
            )
        assert not error.value.outcome_unknown and error.value.contained


def test_admission_failure_cannot_launch(monkeypatch):
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **k: pytest.fail("must not launch")
    )

    def rejected(_):
        raise RuntimeError(SECRET)

    with pytest.raises(ProviderFailure, match="provider_admission_unconfirmed"):
        execute(
            request(),
            binding=binding(),
            base_url="https://example.invalid/v1",
            secret=SECRET,
            deadline_monotonic=time.monotonic() + 5,
            boundary=rejected,
            python_executable=SDK_PYTHON,
        )


def test_owned_child_output_limit_and_stop():
    process = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-c",
            "import sys;sys.stdin.buffer.read();sys.stdout.buffer.write(b'x'*2000000)",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        with pytest.raises(ValueError, match="child_output_limit"):
            _wire_exchange(process, b"{}", time.monotonic() + 3)
    finally:
        assert _stop_group(process)
    with pytest.raises(ProcessLookupError):
        os.killpg(process.pid, 0)


def test_reaped_leader_is_not_accepted_as_group_proof():
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", "pass"], start_new_session=True
    )
    process.wait(timeout=3)
    assert not _stop_group(process)


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}'])
def test_private_pipe_strict_json(raw):
    with pytest.raises(RejectedResponse):
        strict_json(raw)


def test_stop_receipt_failure_keeps_result_uncertain():
    events = []

    def boundary(event):
        events.append(event)
        if event == "stopped":
            raise RuntimeError(SECRET)

    with (
        provider(json.dumps(document()).encode()) as (url, calls),
        pytest.raises(
            ProviderFailure, match="provider_stop_receipt_unconfirmed"
        ) as error,
    ):
        execute(
            request(),
            binding=binding(),
            base_url=url,
            secret=SECRET,
            deadline_monotonic=time.monotonic() + 8,
            boundary=boundary,
            python_executable=SDK_PYTHON,
        )
    assert len(calls) == 1 and events == ["admitted", "stopped"]
    assert error.value.outcome_unknown and error.value.contained
    assert SECRET not in str(error.value)


def test_unconfirmed_group_never_records_stop_or_returns_result(monkeypatch):
    import packages.rdagent_provider.transport as module

    actual_stop = module._stop_group

    def unconfirmed(process):
        # Actually clean up the fixture process; simulate missing confirmation,
        # not a deliberately leaked SDK child or a fabricated successful stop.
        assert actual_stop(process)
        return False

    monkeypatch.setattr(module, "_stop_group", unconfirmed)
    events = []
    with (
        provider(json.dumps(document()).encode()) as (url, calls),
        pytest.raises(
            ProviderFailure, match="provider_containment_unconfirmed"
        ) as error,
    ):
        run(request(), url, events)
    assert len(calls) == 1 and events == ["admitted"]
    assert error.value.outcome_unknown and not error.value.contained


def test_cancellation_preserved_only_after_owned_group_cleanup(monkeypatch):
    import packages.rdagent_provider.transport as module

    events = []

    def interrupted(*_):
        raise KeyboardInterrupt

    monkeypatch.setattr(module, "_wire_exchange", interrupted)
    with pytest.raises(KeyboardInterrupt):
        execute(
            request(),
            binding=binding(),
            base_url="https://example.invalid/v1",
            secret=SECRET,
            deadline_monotonic=time.monotonic() + 5,
            boundary=events.append,
            python_executable=SDK_PYTHON,
        )
    assert events == ["admitted", "stopped"]


def test_actual_identity_callback_precedes_request_and_matches_result():
    from packages.rdagent_provider.transport import ProcessIdentity, ProcessStop

    events, identities = [], []
    with provider(json.dumps(document()).encode()) as (url, calls):

        def observe(phase, value):
            if phase == "started":
                assert type(value) is ProcessIdentity
                assert calls == []  # Private request not delivered before commit.
                assert value.pid == value.process_group == value.session_id
                assert value.parent_pid == os.getpid() and value.start_ticks > 0
            else:
                assert type(value) is ProcessStop
                with pytest.raises(ProcessLookupError):
                    os.killpg(value.identity.pid, 0)
            identities.append((phase, value))

        result = execute(
            request(),
            binding=binding(),
            base_url=url,
            secret=SECRET,
            deadline_monotonic=time.monotonic() + 8,
            boundary=events.append,
            identity_boundary=observe,
            python_executable=SDK_PYTHON,
        )
    assert [phase for phase, _ in identities] == ["started", "stopped"]
    assert identities[0][1] == identities[1][1].identity
    assert result.stop_evidence == identities[1][1]
    assert events == ["admitted", "stopped"] and len(calls) == 1


def test_identity_persistence_failure_never_sends_request_or_claims_stop():
    events, identities = [], []

    def failed(phase, identity):
        identities.append((phase, identity))
        raise RuntimeError("uncertain DB commit")

    with (
        provider(json.dumps(document()).encode()) as (url, calls),
        pytest.raises(ProviderFailure) as error,
    ):
        execute(
            request(),
            binding=binding(),
            base_url=url,
            secret=SECRET,
            deadline_monotonic=time.monotonic() + 8,
            boundary=events.append,
            identity_boundary=failed,
            python_executable=SDK_PYTHON,
        )
    assert calls == [] and events == ["admitted"]
    assert [phase for phase, _ in identities] == ["started"]
    assert error.value.outcome_unknown and error.value.contained
    with pytest.raises(ProcessLookupError):
        os.killpg(identities[0][1].pid, 0)


def test_child_absolute_watchdog_stops_without_parent_timeout():
    import signal
    from pathlib import Path

    import packages.rdagent_provider.sdk_process as module

    deadline = time.monotonic() + 0.5
    # Same trusted contain() code, no SDK/provider call; parent intentionally does
    # NOT enforce the request timeout, proving the child's own original deadline.
    script = (
        "import runpy,time; ns=runpy.run_path("
        + repr(str(Path(module.__file__)))
        + "); ns['contain']("
        + repr(
            {
                "parent_pid": os.getpid(),
                "deadline_monotonic_ns": int(deadline * 1_000_000_000),
                "timeout_seconds": 8.0,
            }
        )
        + "); time.sleep(10)"
    )
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", script],
        start_new_session=True,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert process.wait(timeout=2) == -signal.SIGKILL
        assert time.monotonic() - deadline < 1
        with pytest.raises(ProcessLookupError):
            os.killpg(process.pid, 0)
    finally:
        if process.returncode is None:
            assert _stop_group(process)


@pytest.mark.parametrize(
    "name,version",
    [("openai", "2.53.0"), ("httpx", "0.27.0"), ("openai", None), ("httpx", None)],
)
def test_sdk_version_drift_fails_before_import_or_network(monkeypatch, name, version):
    import importlib.metadata

    import packages.rdagent_provider.sdk_process as module

    def observed(package):
        if package == name:
            if version is None:
                raise importlib.metadata.PackageNotFoundError(package)
            return version
        return module.SDK_VERSIONS[package]

    monkeypatch.setattr(module.importlib.metadata, "version", observed)
    with pytest.raises(RejectedResponse, match="provider_runtime_unavailable"):
        # Empty payload deliberately cannot form a request. The exact version
        # guard must fail before any SDK import or attempted HTTP transmission.
        module.sdk_call({}, transport=object())


@pytest.mark.parametrize("interpreter", [None, "python", "/not/a/real/sdk-python"])
def test_no_implicit_interpreter_fallback(monkeypatch, interpreter):
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **k: pytest.fail("must not launch")
    )
    with pytest.raises(ProviderFailure) as error:
        execute(
            request(),
            binding=binding(),
            base_url="https://example.invalid/v1",
            secret=SECRET,
            deadline_monotonic=time.monotonic() + 5,
            python_executable=interpreter,
        )
    assert error.value.code == "provider_input_rejected"
    assert error.value.contained and not error.value.outcome_unknown
