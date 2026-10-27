"""One no-retry OpenAI-compatible call; private pipe only, no application imports.

The parent owns the hard deadline and group confirmation. This child additionally
kills its own session on parent death/deadline. Provider diagnostics never leave.
"""

import ctypes
import importlib.metadata
import json
import math
import os
import signal
import sys
import time

MAX_PIPE = 1048576


class RejectedResponse(Exception):
    pass


def strict_json(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise RejectedResponse("duplicate_json_key")
            result[key] = value
        return result

    def constant(_):
        raise RejectedResponse("nonfinite_json")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


SDK_VERSIONS = {"openai": "2.54.0", "httpx": "0.28.1"}


def require_sdk_versions():
    try:
        versions = {name: importlib.metadata.version(name) for name in SDK_VERSIONS}
    except importlib.metadata.PackageNotFoundError:
        raise RejectedResponse("provider_runtime_unavailable") from None
    if versions != SDK_VERSIONS:
        raise RejectedResponse("provider_runtime_unavailable")
    return versions


def sdk_call(payload, *, transport=None):
    # Containment is armed in main before this check or any SDK import/network.
    require_sdk_versions()
    # Delayed imports keep process containment ahead of SDK initialization.
    import httpx
    from openai import OpenAI

    class BoundedTransport(httpx.BaseTransport):
        def __init__(self, inner):
            self.inner, self.document = inner, None

        def handle_request(self, request):
            if len(request.content) > payload["max_request_bytes"]:
                raise RejectedResponse("request_too_large")
            response = self.inner.handle_request(request)
            try:
                if response.headers.get("content-encoding", "identity").lower() not in (
                    "",
                    "identity",
                ):
                    raise RejectedResponse("encoded_response")
                length = response.headers.get("content-length")
                if length is not None and (
                    not length.isdecimal()
                    or int(length) > payload["max_response_bytes"]
                ):
                    raise RejectedResponse("response_too_large")
                chunks, count = [], 0
                source = (
                    (response.content,)
                    if response.is_stream_consumed
                    else response.iter_raw()
                )
                for chunk in source:
                    count += len(chunk)
                    if count > payload["max_response_bytes"]:
                        raise RejectedResponse("response_too_large")
                    chunks.append(chunk)
                raw = b"".join(chunks)
                if 300 <= response.status_code < 400:
                    raise RejectedResponse("redirect_response")
                if response.status_code == 200:
                    self.document = strict_json(raw)
                return httpx.Response(
                    response.status_code,
                    headers=response.headers,
                    content=raw,
                    request=request,
                )
            finally:
                response.close()

        def close(self):
            self.inner.close()

    bounded = BoundedTransport(
        transport or httpx.HTTPTransport(retries=0, trust_env=False)
    )
    request = payload["request"]
    with (
        httpx.Client(
            transport=bounded,
            trust_env=False,
            follow_redirects=False,
            timeout=payload["timeout_seconds"],
        ) as http,
        OpenAI(
            api_key=payload["secret"],
            base_url=payload["base_url"],
            max_retries=0,
            http_client=http,
            default_headers={"Accept-Encoding": "identity"},
        ) as client,
    ):
        if request["kind"] == "chat":
            options = {
                "model": request["model"],
                "messages": request["messages"],
                "max_tokens": request["max_output_tokens"],
                "temperature": 0,
                "stream": False,
                "timeout": payload["timeout_seconds"],
            }
            if request["response_format"] is not None:
                options["response_format"] = request["response_format"]
            client.chat.completions.create(**options)
        else:
            client.embeddings.create(
                model=request["model"],
                input=request["inputs"],
                dimensions=request["dimensions"],
                encoding_format="float",
                timeout=payload["timeout_seconds"],
            )
    # Inspect original JSON primitives, not SDK-coerced bool/string token counts.
    return validate_document(bounded.document, request, payload["synthetic"])


def validate_document(value, request, synthetic):
    if type(value) is not dict:
        raise RejectedResponse("invalid_response")
    identifier = value.get("id")
    if identifier is not None and (
        type(identifier) is not str
        or not 1 <= len(identifier.encode()) <= 256
        or any(ord(c) < 32 for c in identifier)
    ):
        raise RejectedResponse("invalid_identifier")
    usage = value.get("usage")
    input_tokens = output_tokens = total_tokens = None
    if usage is not None:
        if type(usage) is not dict:
            raise RejectedResponse("invalid_usage")
        names = ("prompt_tokens", "completion_tokens", "total_tokens")
        for name in names:
            count = usage.get(name)
            if count is not None and (
                type(count) is not int or not 0 <= count <= 100000000
            ):
                raise RejectedResponse("invalid_usage")
        input_tokens, output_tokens, total_tokens = (usage.get(name) for name in names)
        if request["kind"] == "embedding" and output_tokens is not None:
            raise RejectedResponse("invalid_usage")
        if input_tokens is not None and total_tokens is not None:
            if request["kind"] == "embedding" and input_tokens != total_tokens:
                raise RejectedResponse("invalid_usage")
            if (
                output_tokens is not None
                and input_tokens + output_tokens != total_tokens
            ):
                raise RejectedResponse("invalid_usage")
    result = {
        "content": None,
        "vectors": None,
        "finish_reason": None,
        "provider_request_id": identifier,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "usage_source": "synthetic_provider" if synthetic else "provider",
        "billing_status": "unknown",
    }
    if request["kind"] == "chat":
        choices = value.get("choices")
        if (
            type(choices) is not list
            or len(choices) != 1
            or type(choices[0]) is not dict
        ):
            raise RejectedResponse("invalid_choices")
        choice = choices[0]
        message = choice.get("message")
        if (
            type(choice.get("index")) is not int
            or choice["index"] != 0
            or choice.get("finish_reason") not in ("stop", "length")
            or type(message) is not dict
            or message.get("role") != "assistant"
            or message.get("tool_calls")
            or message.get("function_call")
            or message.get("refusal")
            or type(message.get("content")) is not str
            or not 1 <= len(message["content"].encode()) <= 32768
        ):
            raise RejectedResponse("invalid_chat")
        if (
            request["response_format"] is not None
            and choice["finish_reason"] == "stop"
            and type(strict_json(message["content"])) is not dict
        ):
            raise RejectedResponse("invalid_json_object")
        result.update(content=message["content"], finish_reason=choice["finish_reason"])
    else:
        data = value.get("data")
        if type(data) is not list or len(data) != len(request["inputs"]):
            raise RejectedResponse("invalid_embeddings")
        vectors = []
        for index, row in enumerate(data):
            if (
                type(row) is not dict
                or type(row.get("index")) is not int
                or row["index"] != index
                or row.get("object") != "embedding"
            ):
                raise RejectedResponse("invalid_embedding_index")
            vector = row.get("embedding")
            if (
                type(vector) is not list
                or len(vector) != request["dimensions"]
                or any(
                    type(x) not in (int, float) or not math.isfinite(x) for x in vector
                )
            ):
                raise RejectedResponse("invalid_embedding_values")
            vectors.append(vector)
        result["vectors"] = vectors
    return result


def execute_payload(payload, *, transport=None):
    try:
        return {"ok": True, "response": sdk_call(payload, transport=transport)}
    except Exception:  # noqa: BLE001 - sanitize private SDK/credential errors
        # A malformed/error response may still have consumed tokens. Never retry
        # or label this an unbilled failure; raw SDK exception text is secret.
        return {"ok": False, "error_class": "provider_outcome_unknown"}


def contain(payload):
    if sys.platform != "linux" or os.getpgrp() != os.getpid():
        raise RuntimeError("containment_unavailable")
    group = os.getpid()

    def stop(*_):
        os.killpg(group, signal.SIGKILL)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGALRM, stop)
    libc = ctypes.CDLL(None, use_errno=True)
    if (
        libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0
        or os.getppid() != payload["parent_pid"]
    ):
        stop()
    remaining = (payload["deadline_monotonic_ns"] - time.monotonic_ns()) / 1_000_000_000
    if not 0 < remaining <= 8.0:
        stop()
    signal.setitimer(signal.ITIMER_REAL, remaining)


def main():
    try:
        # Public fixed launch metadata, never endpoint/credential arguments. Arm
        # before reading stdin so even a blocked identity-commit callback cannot
        # leave a child waiting past the original deadline.
        if len(sys.argv) != 3:
            raise ValueError("launch_protocol")
        launch = {
            "parent_pid": int(sys.argv[1]),
            "deadline_monotonic_ns": int(sys.argv[2]),
        }
        contain(launch)
        raw = sys.stdin.buffer.read(MAX_PIPE + 1)
        if len(raw) > MAX_PIPE:
            raise ValueError("bound")
        payload = strict_json(raw)
        if any(payload.get(key) != value for key, value in launch.items()):
            raise ValueError("launch_protocol")
        result = execute_payload(payload)
    except Exception:  # noqa: BLE001 - sanitize private SDK/credential errors
        result = {"ok": False, "error_class": "provider_outcome_unknown"}
    sys.stdout.write(json.dumps(result, ensure_ascii=True, allow_nan=False))


if __name__ == "__main__":
    main()
