"""Closed source-pinned provider validation diagnostics; no retained errors."""

from hashlib import sha256
from pathlib import Path
from types import CodeType

from pydantic import ValidationError

_FIELDS = {
    "schema_version",
    "kind",
    "model_profile_id",
    "model_binding_sha256",
    "model",
    "inputs",
    "dimensions",
    "admitted_monotonic_ns",
    "deadline_monotonic_ns",
    "maximum_duration_ms",
    "boot_id",
    "identity",
    "observed_monotonic_ns",
    "leader_reaped",
    "process_group_absent",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "provider_request_id",
    "usage_source",
    "billing_status",
    "vectors",
    "usage",
    "transport_id",
    "request_sha256",
    "process_id",
    "process_group_id",
}
_TYPES = {
    "value_error",
    "missing",
    "int_type",
    "float_type",
    "bool_type",
    "string_type",
    "uuid_type",
    "uuid_parsing",
    "string_pattern_mismatch",
    "literal_error",
    "finite_number",
    "greater_than",
    "greater_than_equal",
    "less_than_equal",
    "too_short",
    "too_long",
    "string_too_short",
    "string_too_long",
    "tuple_type",
    "extra_forbidden",
    "model_type",
}


def _field(error):
    if error.error_count() != 1:
        return {}
    (detail,) = error.errors(
        include_url=False, include_context=False, include_input=False
    )
    where, kind = detail["loc"], detail["type"]
    if type(kind) is not str or kind not in _TYPES:
        return {}
    if not where:
        field = "model"
    elif (
        len(where) <= 4
        and type(where[0]) is str
        and where[0] in _FIELDS
        and all(
            type(part) is int or (type(part) is str and part in _FIELDS)
            for part in where
        )
    ):
        # Array indices and nested fields are deliberately not exported.
        field = where[0]
    else:
        return {}
    return {"validation_field": field, "validation_type": kind}


def _site(trace):
    from backend.app import rdagent_provider_broker as broker
    from packages.rdagent_provider import codec

    for module, digest in (
        (broker, "b2beef8c3e8f2e9aadbc049c904bab5b7a47db9598c2839bcc8e59357695077a"),
        (codec, "a86270b57341438be0a0ebb3d95bc14abe01e117060a979814e14400d862be78"),
    ):
        try:
            if sha256(Path(module.__file__).read_bytes()).hexdigest() != digest:
                return None
        except (OSError, TypeError):
            return None
    submit = broker.submit_provider_request.__code__
    callback = next(
        (
            code
            for code in submit.co_consts
            if type(code) is CodeType
            and code.co_qualname == "submit_provider_request.<locals>.identity_boundary"
        ),
        None,
    )
    roles = (
        (submit, "submit"),
        (callback, "callback"),
        (broker._settle.__code__, "settle"),
        (codec.parse_request.__code__, "parse"),
        (codec.validate_response.__code__, "validate"),
    )
    sites = {}
    for _ in range(64):
        if trace is None:
            break
        for code, label in roles:
            if trace.tb_frame.f_code is code:
                if label in sites:
                    return None
                sites[label] = trace.tb_lineno
        trace = trace.tb_next
    if trace is not None:
        return None
    line = sites.get("submit", 0)
    callback_line = sites.get("callback")
    if line == 160 and sites.get("parse") == 56:
        return "request"
    if line == 182:
        return "replay"
    if line in (164, 165, 186, 193, 211):
        return "configuration"
    if 242 <= line <= 248:
        return "sdk_intent"
    if 282 <= line <= 290:
        return {264: "sdk_started", 266: "sdk_stopped"}.get(callback_line)
    if 291 <= line <= 298:
        return "usage"
    if line == 304:
        return "embedding_response"
    if line == 306 and ("validate" in sites or "parse" in sites):
        return "response_validation"
    if 316 <= line <= 323 and "settle" in sites:
        return "settlement"
    return None


def broker_validation_cut(error):
    if type(error) is not ValidationError:
        return {}
    site = _site(error.__traceback__)
    if site is None:
        return {}
    return {"validation_boundary": site, **_field(error)}
