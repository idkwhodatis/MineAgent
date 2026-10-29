"""Host-resolved data projection; parsing this DTO never grants permission."""

from hashlib import sha256

from pydantic import Field, field_validator, model_validator

from packages.factors.contracts import Contract, Sha256
from packages.research_inputs.contracts import InputVersionRef, ProposalKind


class ResolvedInputVersion(Contract):
    ref: InputVersionRef
    kind: ProposalKind
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=2048)
    text_sha256: Sha256

    @field_validator("title", "text")
    @classmethod
    def bounded_data(cls, value, info):
        maximum = 800 if info.field_name == "title" else 8192
        if not value.strip() or len(value.encode("utf-8")) > maximum:
            raise ValueError("Bounded nonempty input data required")
        # Preserve the exact existing manual SteerVersion byte contract rather
        # than silently normalizing its text or rejecting accepted CRLF versions.
        ref = info.data.get("ref")
        if (
            info.field_name == "text"
            and ref is not None
            and ref.origin == "manual_user_submission"
        ):
            if "\0" in value:
                raise ValueError("Manual steer text cannot contain NUL")
            return value
        if any(
            ord(c) == 127
            or (ord(c) < 32 and not (info.field_name == "text" and c in "\n\t"))
            for c in value
        ):
            raise ValueError("Unsupported input control character")
        return value

    @model_validator(mode="after")
    def exact_text(self):
        if self.ref.origin == "unknown":
            raise ValueError("Unknown input origin cannot be consumed")
        if sha256(self.text.encode("utf-8")).hexdigest() != self.text_sha256:
            raise ValueError("Resolved text commitment differs")
        return self
