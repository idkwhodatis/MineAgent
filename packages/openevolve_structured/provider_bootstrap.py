"""Public normal-provider controller declaration, distinct from fixture bootstrap.

No endpoint, credential, SDK interpreter or model-call permission is serialized.
A host must resolve this against the immutable run, current epoch and admission.
"""

from typing import Literal

from pydantic import model_validator

from packages.factors.contracts import digest

from .candidate import _raw_fields
from .contracts import SHA, StructuredBootstrap
from .provider_contracts import OEChatBinding


class ProviderStructuredBootstrap(StructuredBootstrap):
    schema_version: Literal["mineagent.oe-structured-bootstrap.v2"] = (
        "mineagent.oe-structured-bootstrap.v2"
    )
    model_binding: OEChatBinding
    model_binding_sha256: SHA

    @model_validator(mode="after")
    def normal_binding(self):
        binding = OEChatBinding.model_validate(_raw_fields(self.model_binding))
        if self.model_binding_sha256 != digest(binding):
            raise ValueError("Exact public operator model binding required")
        return self


def runtime_profile_sha256():
    return digest(
        {
            "schema_version": "mineagent.oe-structured-provider-runtime-profile.v1",
            "language": "json",
            "rewrite": "complete_canonical_closed_program",
            "maximum_candidate_bytes": 32768,
            "maximum_response_bytes": 262144,
            "maximum_request_bytes": 262144,
            "maximum_output_tokens": 4096,
            "maximum_sdk_deadline_ms": 8000,
            "maximum_feedback_bytes": 8192,
            "evaluator_timeout_seconds": 90,
            "provider_access": "host_only",
            "resume": False,
        }
    )
