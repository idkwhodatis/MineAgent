"""Initial immutable compatibility limits, not enabled process admission."""

from typing import Literal

from pydantic import field_validator

from packages.research_runtime.broker_contracts import Contract, digest


class RuntimeProfile(Contract):
    schema_version: Literal["mineagent.openevolve-runtime-profile.v1"] = (
        "mineagent.openevolve-runtime-profile.v1"
    )
    capability: Literal["disabled_native_fixture"] = "disabled_native_fixture"
    maximum_population: Literal[8] = 8
    maximum_archive: Literal[8] = 8
    maximum_mutation_slots: Literal[2] = 2
    maximum_source_bytes: Literal[32768] = 32768
    maximum_source_characters: Literal[32768] = 32768
    maximum_request_bytes: Literal[786432] = 786432
    maximum_message_characters: Literal[262144] = 262144
    maximum_response_bytes: Literal[32768] = 32768
    maximum_output_tokens: Literal[32768] = 32768
    maximum_feedback_bytes: Literal[16384] = 16384
    maximum_checkpoint_bytes: Literal[8388608] = 8388608
    workers: Literal[1] = 1
    islands: Literal[1] = 1
    top_programs: Literal[1] = 1
    diverse_programs: Literal[1] = 1
    start_method: Literal["spawn"] = "spawn"
    recycling: Literal["disabled"] = "disabled"
    model_calls_per_intent: Literal[1] = 1
    model_profile: Literal["openevolve-fixture.v1"] = "openevolve-fixture.v1"
    evaluator_timeout_seconds: Literal[90] = 90
    evaluator_supervisor_deadline_seconds: Literal[60] = 60
    unit_deadline_seconds: Literal[600] = 600

    @field_validator("*", mode="before")
    @classmethod
    def exact_numeric_literals(cls, value, info):
        default = cls.model_fields[info.field_name].default
        if type(default) is int and type(value) is not int:
            raise ValueError("Runtime profile numeric literals require exact integers")
        return value

    @property
    def sha256(self):
        return digest(self)


PROFILE = RuntimeProfile()


def validate_source_bound(source: bytes):
    from .source import canonical_source

    decoded = canonical_source(source)
    if (
        len(source) > PROFILE.maximum_source_bytes
        or len(decoded) > PROFILE.maximum_source_characters
    ):
        raise ValueError("Source exceeds immutable runtime profile")
    return decoded
