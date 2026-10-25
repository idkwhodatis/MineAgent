"""Closed neutral process evidence; no executable commands or lease credentials."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC
from uuid import UUID


class ProcessError(RuntimeError):
    def __init__(self, code="research_process_uncertain"):
        self.code = code
        super().__init__(code)


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def sha256(value):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ProcessError("research_process_identity")
    return value


@dataclass(frozen=True)
class ProcessAdmission:
    id: UUID
    owner_id: UUID
    run_id: UUID
    attempt_id: UUID
    generation: int
    role: str
    logical_operation_id: UUID
    launch_ordinal: int
    profile: str
    input_sha256: str
    reservation_id: UUID
    image_id: str
    ticket_sha256: str

    def __post_init__(self):
        from packages.research_runtime.process_profiles import profile_for

        for value in (
            self.id,
            self.owner_id,
            self.run_id,
            self.attempt_id,
            self.logical_operation_id,
            self.reservation_id,
        ):
            if type(value) is not UUID:
                raise ProcessError("research_process_identity")
        if type(self.generation) is not int or self.generation < 1:
            raise ProcessError("research_process_identity")
        if type(self.launch_ordinal) is not int or self.launch_ordinal < 1:
            raise ProcessError("research_process_identity")
        profile_for(self.profile, self.role)
        sha256(self.input_sha256)
        sha256(self.ticket_sha256)
        if type(self.image_id) is not str or not self.image_id.startswith("sha256:"):
            raise ProcessError("research_process_image")
        sha256(self.image_id[7:])

    def public(self):
        return {
            key: str(value) if isinstance(value, UUID) else value
            for key, value in self.__dict__.items()
        }


@dataclass(frozen=True)
class ContainmentSummary:
    total: int
    stopped: int

    @property
    def confirmed(self):
        return self.total == self.stopped


def validate_launch_intent(intent, admission):
    """Offline immutable intent grammar; historical evidence has no now check."""
    from datetime import datetime

    from packages.research_runtime.process_profiles import profile_for

    if (
        type(intent) is not dict
        or set(intent)
        != {"schema_version", "intent_id", "admission_sha256", "issued_at", "not_after"}
        or intent["schema_version"] != "mineagent.research-process-intent.v1"
        or intent["admission_sha256"] != digest(admission.public())
    ):
        raise ProcessError("research_process_launch_authority")
    try:
        if str(UUID(intent["intent_id"])) != intent["intent_id"]:
            raise ValueError()
        issued, end = (
            datetime.fromisoformat(intent[key]) for key in ("issued_at", "not_after")
        )
        if (
            issued.tzinfo is None
            or end.tzinfo is None
            or issued.utcoffset() != UTC.utcoffset(issued)
            or end.utcoffset() != UTC.utcoffset(end)
            or not 0
            < (end - issued).total_seconds()
            <= profile_for(admission.profile, admission.role).wall_seconds
        ):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise ProcessError("research_process_launch_authority") from None
    return intent
