"""Kind-3 protected candidate rejection; data grammar, never launch authority.

A measured child exit is not a financial-quality verdict. Infrastructure failures
and missing output without this exact protected frame remain unknown.
"""

import hashlib
import json
from typing import Literal

from pydantic import Field, field_validator, model_validator

from packages.research_runtime.numeric_process_journal import validate_numeric_journal
from packages.research_runtime.process_contracts import ProcessError, digest
from packages.research_runtime.process_declarations_v2 import SHA, Closed


def parse_rejection_payload(raw: bytes) -> int:
    from runtimes.factor_execution.protected_output import validate_rejection

    if type(raw) is not bytes or not 0 < len(raw) <= 1024:
        raise ProcessError("numeric_rejection_payload")
    try:
        value = json.loads(raw)
        code = value["child_exit_code"]
        # Exact byte reconstruction also rejects duplicate/extra keys, whitespace,
        # bool-as-int, noncanonical numbers, and non-UTF8 alternate encodings.
        validate_rejection(raw, expected_basename="result.h5", expected_exit_code=code)
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ProcessError("numeric_rejection_payload") from None
    return code


class NumericRejection(Closed):
    schema_version: Literal["mineagent.numeric-process-rejection.v1"]
    launch_sha256: SHA
    identity_sha256: SHA
    stop_sha256: SHA
    container_id: SHA
    protocol: Literal["MANU2-kind3"]
    basename: Literal["result.h5"]
    payload_utf8: str = Field(min_length=1, max_length=1024)
    payload_sha256: SHA
    child_exit_code: int = Field(ge=-64, le=255)
    descendants_reaped: Literal[True]
    complete_eof: Literal[True]

    @field_validator("descendants_reaped", "complete_eof", mode="before")
    @classmethod
    def literal_flags(cls, value):
        if value is not True:
            raise ValueError("Numeric rejection requires literal true flags")
        return value

    @model_validator(mode="after")
    def exact_payload(self):
        raw = self.payload_utf8.encode("utf-8")
        if (
            parse_rejection_payload(raw) != self.child_exit_code
            or hashlib.sha256(raw).hexdigest() != self.payload_sha256
            or type(self.descendants_reaped) is not bool
            or type(self.complete_eof) is not bool
        ):
            raise ValueError("Numeric rejection payload differs")
        return self

    @property
    def outcome(self) -> Literal["child_nonzero", "output_rejected"]:
        return "child_nonzero" if self.child_exit_code != 0 else "output_rejected"


def validate_rejection_journal(launch, identity, stop, rejection):
    launch, identity, stop, _ = validate_numeric_journal(launch, identity, stop)
    rejection = NumericRejection.model_validate(rejection)
    if (
        launch.intent.admission.role != "factor_cutoff_candidate"
        or not stop.deadline_bound_met
        or rejection.launch_sha256 != digest(launch.model_dump(mode="json"))
        or rejection.identity_sha256 != digest(identity.model_dump(mode="json"))
        or rejection.stop_sha256 != digest(stop.model_dump(mode="json"))
        or rejection.container_id != identity.container_id
    ):
        raise ProcessError("numeric_rejection_identity")
    return launch, identity, stop, rejection
