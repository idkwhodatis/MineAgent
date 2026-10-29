"""Separate closed027 runtime limits; no registration or caller image authority."""

from dataclasses import asdict, dataclass

from packages.factors.contracts import digest

from .candidate import project_candidate
from .contracts import StructuredSearchSpec


@dataclass(frozen=True)
class RuntimeProfile:
    model_profile: str = "openevolve-structured-fixture.v1"
    maximum_source_bytes: int = 32768
    maximum_response_bytes: int = 32768
    maximum_message_characters: int = 262144
    maximum_request_bytes: int = 786432
    maximum_output_tokens: int = 32768
    maximum_feedback_bytes: int = 16384
    evaluator_timeout_seconds: int = 90


PROFILE = RuntimeProfile()


def profile_sha256():
    return digest(
        {
            "schema_version": "mineagent.oe-structured-runtime-profile.v1",
            **asdict(PROFILE),
        }
    )


def project_current(raw):
    from .observer_transport import bootstrap, canonical

    spec = StructuredSearchSpec.model_validate_json(canonical(bootstrap()["spec"]))
    return project_candidate(raw, spec.domain)


def validate_source_bound(raw):
    # Upstream calls its opaque program text "source"; this027 branch only
    # accepts the complete canonical JSON document, never Python/source regions.
    project_current(raw)
    return raw.decode("utf-8")
