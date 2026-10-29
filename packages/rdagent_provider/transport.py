"""Host-only bounded SDK subprocess transport; no credentials are discovered here.

The durable broker owns admission/replay/accounting. One invocation sends at most
one request and never retries; a transport failure cannot authorize replacement.
"""

import json
import math
import os
import selectors
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel

from .contracts import (
    RDModelBinding,
    RDProviderChatRequest,
    RDProviderEmbeddingRequest,
    require_request_binding,
)
from .sdk_process import MAX_PIPE, strict_json, validate_document


class ProviderFailure(RuntimeError):
    def __init__(self, code, *, outcome_unknown=True, contained=False):
        self.code = code
        self.outcome_unknown = outcome_unknown
        self.contained = contained
        super().__init__(code)


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    start_ticks: int
    process_group: int
    session_id: int
    parent_pid: int
    boot_id: str
    deadline_monotonic_ns: int


@dataclass(frozen=True)
class ProcessStop:
    identity: ProcessIdentity
    observed_monotonic_ns: int
    leader_reaped: bool = True
    process_group_absent: bool = True


def _observe_process(process, deadline_ns):
    def stat():
        raw = Path(f"/proc/{process.pid}/stat").read_bytes()
        if len(raw) > 4096 or not raw.startswith(f"{process.pid} (".encode()):
            raise ValueError("process_identity")
        fields = raw.rsplit(b") ", 1)[1].split()
        return tuple(int(fields[i]) for i in (1, 2, 3, 19))

    first = stat()
    group, session = os.getpgid(process.pid), os.getsid(process.pid)
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    if (
        str(UUID(boot)) != boot
        or stat() != first
        or first[:3] != (os.getpid(), process.pid, process.pid)
        or group != process.pid
        or session != process.pid
        or first[3] <= 0
    ):
        raise ValueError("process_identity")
    return ProcessIdentity(
        process.pid,
        first[3],
        group,
        session,
        os.getpid(),
        boot,
        deadline_ns,
    )


@dataclass(frozen=True)
class ProviderResult:
    content: str | None
    vectors: tuple[tuple[float, ...], ...] | None
    finish_reason: str | None
    provider_request_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    usage_source: str
    billing_status: str
    contained: bool = True
    stop_evidence: ProcessStop | None = None


def _wire_exchange(process, wire, deadline):
    """Bound both directions without communicate's unbounded output buffer/reap."""
    chunks, size, offset = [], 0, 0
    with selectors.DefaultSelector() as selector:
        for stream, event in (
            (process.stdin, selectors.EVENT_WRITE),
            (process.stdout, selectors.EVENT_READ),
        ):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, event)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            events = selector.select(remaining)
            if not events:
                raise TimeoutError
            for key, _ in events:
                if key.fileobj is process.stdin:
                    try:
                        written = os.write(key.fd, wire[offset : offset + 65536])
                    except BrokenPipeError:
                        raise ValueError("child_input_closed") from None
                    offset += written
                    if offset == len(wire):
                        selector.unregister(key.fileobj)
                        process.stdin.close()
                else:
                    data = os.read(key.fd, 65536)
                    if not data:
                        selector.unregister(key.fileobj)
                        process.stdout.close()
                    else:
                        size += len(data)
                        if size > MAX_PIPE:
                            raise ValueError("child_output_limit")
                        chunks.append(data)
    return b"".join(chunks)


def _stop_group(process):
    """Leader has NOT been reaped; its PID cannot be recycled before killpg.

    Even successful stdout is not a stop proof. Kill residual group members then
    reap the leader and confirm group absence. Zombies/permission errors fail
    closed rather than pretending that a leader exit proves whole-tree exit.
    """
    if process.returncode is not None:
        return False
    deadline = time.monotonic() + 2.0
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        return False
    try:
        process.wait(timeout=max(0.001, deadline - time.monotonic()))
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        for stream in (process.stdin, process.stdout):
            if stream is not None and not stream.closed:
                stream.close()
    while time.monotonic() < deadline:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        time.sleep(0.01)
    return False


def _validated_result(value, request, synthetic):
    expected = {
        "content",
        "vectors",
        "finish_reason",
        "provider_request_id",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "usage_source",
        "billing_status",
    }
    if type(value) is not dict or set(value) != expected:
        raise ValueError("result_schema")
    if (
        value["usage_source"] != ("synthetic_provider" if synthetic else "provider")
        or value["billing_status"] != "unknown"
    ):
        raise ValueError("result_provenance")
    # Reuse strict primitive validation without trusting the child's dataclass.
    document = {
        "id": value["provider_request_id"],
        "usage": {
            "prompt_tokens": value["input_tokens"],
            "completion_tokens": value["output_tokens"],
            "total_tokens": value["total_tokens"],
        },
    }
    if request["kind"] == "chat":
        if value["vectors"] is not None:
            raise ValueError("result_kind")
        document["choices"] = [
            {
                "index": 0,
                "finish_reason": value["finish_reason"],
                "message": {"role": "assistant", "content": value["content"]},
            }
        ]
    else:
        if (
            value["content"] is not None
            or value["finish_reason"] is not None
            or type(value["vectors"]) is not list
        ):
            raise ValueError("result_kind")
        document["data"] = [
            {"index": i, "object": "embedding", "embedding": row}
            for i, row in enumerate(value["vectors"])
        ]
    checked = validate_document(document, request, synthetic)
    if checked != value:
        raise ValueError("result_changed")
    if checked["vectors"] is not None:
        checked["vectors"] = tuple(
            tuple(float(x) for x in row) for row in checked["vectors"]
        )
    return ProviderResult(**checked)


def _raw_fields(value):
    # Serializer output may coerce forged model_copy booleans into integer slots.
    # Rebuild original values first; do not trust a preconstructed model instance.
    if isinstance(value, BaseModel):
        return {
            name: _raw_fields(getattr(value, name)) for name in type(value).model_fields
        }
    if isinstance(value, (tuple, list)):
        return [_raw_fields(item) for item in value]
    if isinstance(value, dict):
        return {key: _raw_fields(item) for key, item in value.items()}
    return value


def execute(
    request,
    *,
    binding,
    base_url,
    secret,
    deadline_monotonic,
    boundary=None,
    python_executable=None,
    identity_boundary=None,
    protocol=None,
):
    """Only a host broker may supply endpoint, secret and SDK executable.

    ``boundary('admitted')`` must commit before launch. ``boundary('stopped')``
    runs only after positive whole-group confirmation, including failure paths.
    ``identity_boundary('started', identity)`` must durably bind the actual
    PID/start ticks/session/boot before any private request bytes are delivered.
    Its stopped event carries the same identity and positive group-absence proof.
    No callback means this is a component call, not persisted process authority.
    """
    try:
        if protocol is None:
            binding_type = RDModelBinding
            request_types = (RDProviderChatRequest, RDProviderEmbeddingRequest)
            validate_binding = require_request_binding
        elif protocol == "mineagent.oe-structured-provider-chat.v1":
            # Explicit successor opt-in only: default RD dispatch is unchanged.
            # These public contracts import no operator endpoint or native SDK.
            from packages.openevolve_structured.provider_contracts import (
                OEChatBinding,
                OEChatRequest,
            )
            from packages.openevolve_structured.provider_contracts import (
                require_request_binding as validate_binding,
            )

            binding_type = OEChatBinding
            request_types = (OEChatRequest,)
        else:
            raise ValueError("protocol")
        if type(binding) is not binding_type or type(request) not in request_types:
            raise ValueError("types")
        # Reparse JSON for UUID/tuple semantics, but inspect integer primitives first.
        raw_request = _raw_fields(request)
        raw_binding = _raw_fields(binding)
        if any(
            type(raw_request[k]) is not int
            for k in ("max_output_tokens", "temperature_millionths", "dimensions")
            if k in raw_request
        ):
            raise ValueError("integer")
        request = type(request).model_validate_json(
            json.dumps(raw_request, allow_nan=False)
        )
        binding = binding_type.model_validate_json(
            json.dumps(raw_binding, allow_nan=False)
        )
        validate_binding(request, binding)
        url = urlsplit(base_url)
        if (
            url.scheme not in ("https", "http")
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or (
                url.scheme == "http"
                and url.hostname not in ("127.0.0.1", "localhost", "::1")
            )
        ):
            raise ValueError("endpoint")
        if (
            type(secret) is not str
            or not 1 <= len(secret.encode()) <= 8192
            or any(ord(x) < 32 for x in secret)
        ):
            raise ValueError("credential")
        if type(deadline_monotonic) not in (int, float) or not math.isfinite(
            deadline_monotonic
        ):
            raise ValueError("deadline")
        deadline_ns = min(
            int(deadline_monotonic * 1_000_000_000),
            time.monotonic_ns() + binding.request_deadline_ms * 1_000_000,
        )
        deadline = deadline_ns / 1_000_000_000
        remaining = (deadline_ns - time.monotonic_ns()) / 1_000_000_000
        if remaining <= 0 or sys.platform != "linux":
            raise ValueError("deadline_or_platform")
        if not isinstance(python_executable, (str, Path)):
            raise TypeError("sdk_interpreter_unconfigured")
        python_executable = os.fspath(python_executable)
        if (
            not os.path.isabs(python_executable)
            or not Path(python_executable).is_file()
            or not os.access(python_executable, os.X_OK)
        ):
            raise ValueError("sdk_interpreter_unavailable")
        payload = {
            "request": raw_request,
            "secret": secret,
            "base_url": base_url,
            "parent_pid": os.getpid(),
            "timeout_seconds": remaining,
            "deadline_monotonic_ns": deadline_ns,
            "max_request_bytes": binding.max_request_bytes,
            "max_response_bytes": binding.max_response_bytes,
            "synthetic": binding.synthetic,
        }
        wire = json.dumps(payload, ensure_ascii=True, allow_nan=False).encode()
        if len(wire) > MAX_PIPE:
            raise ValueError("pipe_limit")
    except Exception:  # noqa: BLE001 - sanitize private SDK/credential errors
        raise ProviderFailure(
            "provider_input_rejected", outcome_unknown=False, contained=True
        ) from None
    if boundary is not None:
        try:
            boundary("admitted")
        except Exception:  # noqa: BLE001 - sanitize private SDK/credential errors
            raise ProviderFailure(
                "provider_admission_unconfirmed", contained=True
            ) from None
    process = None
    identity = None
    stop_evidence = None
    identity_committed = identity_boundary is None

    def stop_owned():
        if identity is not None:
            try:
                observed = _observe_process(process, deadline_ns)
                if observed != identity:
                    return False
            except (OSError, ValueError, IndexError):
                return False
        return _stop_group(process)

    def confirm_stop():
        nonlocal stop_evidence
        if identity is not None:
            stop_evidence = ProcessStop(identity, time.monotonic_ns())
        if identity_boundary is not None:
            if identity is None or not identity_committed:
                raise ProviderFailure(
                    "provider_identity_receipt_unconfirmed", contained=True
                )
            identity_boundary("stopped", stop_evidence)
        if boundary is not None:
            boundary("stopped")

    try:
        if time.monotonic() >= deadline:
            raise TimeoutError
        process = subprocess.Popen(
            [
                python_executable,
                "-I",
                str(Path(__file__).with_name("sdk_process.py")),
                str(os.getpid()),
                str(deadline_ns),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
            env={"PATH": os.defpath, "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"},
        )
        identity = _observe_process(process, deadline_ns)
        if identity_boundary is not None:
            identity_boundary("started", identity)
            identity_committed = True
        output = _wire_exchange(process, wire, deadline)
    except BaseException as error:
        confirmed = process is None or stop_owned()
        if confirmed:
            try:
                confirm_stop()
            except Exception:  # noqa: BLE001 - sanitize private SDK/credential errors
                raise ProviderFailure(
                    "provider_stop_receipt_unconfirmed", contained=True
                ) from None
        if not isinstance(error, Exception):
            # Preserve cancellation/system-exit semantics after bounded cleanup.
            # If confirmation failed, absence of a stopped callback retains hold.
            raise
        raise ProviderFailure(
            "provider_outcome_unknown"
            if confirmed
            else "provider_containment_unconfirmed",
            contained=confirmed,
        ) from None
    if not stop_owned():
        raise ProviderFailure("provider_containment_unconfirmed")
    try:
        confirm_stop()
    except Exception:  # noqa: BLE001 - private persistence diagnostics
        raise ProviderFailure(
            "provider_stop_receipt_unconfirmed", contained=True
        ) from None
    try:
        result = strict_json(output)
        if (
            type(result) is not dict
            or set(result) != {"ok", "response"}
            or result["ok"] is not True
        ):
            raise ValueError("protocol")
        from dataclasses import replace

        checked = _validated_result(result["response"], raw_request, binding.synthetic)
        return replace(checked, stop_evidence=stop_evidence)
    except Exception:  # noqa: BLE001 - sanitize private SDK/credential errors
        raise ProviderFailure("provider_outcome_unknown", contained=True) from None
