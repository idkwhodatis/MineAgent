"""Bounded content contracts, not bearer grants or proof of process authority."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from packages.factors.contracts import Contract, Sha256, source_sha256


class ModelResponsePart(Contract):
    member_id: UUID
    outcome_id: UUID
    response_object_id: UUID
    response_sha256: Sha256


class ParseFailureReceipt(Contract):
    schema_version: Literal["mineagent.rdagent-parse-failure.v1"] = (
        "mineagent.rdagent-parse-failure.v1"
    )
    call_id: UUID
    orchestrator_admission_id: UUID
    parser_profile: Literal["mineagent.rdagent-json-parser.v1"] = (
        "mineagent.rdagent-json-parser.v1"
    )
    reason: Literal["invalid_json", "invalid_response_schema"]
    parts: tuple[ModelResponsePart, ...] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def unique(self):
        if len({part.member_id for part in self.parts}) != len(self.parts):
            raise ValueError("Duplicate parse-failure response part")
        return self


class ResponseAssemblyDocument(Contract):
    schema_version: Literal["mineagent.rdagent-response-assembly.v1"] = (
        "mineagent.rdagent-response-assembly.v1"
    )
    call_id: UUID
    orchestrator_admission_id: UUID
    parser_profile: Literal["mineagent.rdagent-json-parser.v1"] = (
        "mineagent.rdagent-json-parser.v1"
    )
    parts: tuple[ModelResponsePart, ...] = Field(min_length=1, max_length=6)
    source: str = Field(min_length=1, max_length=131072)
    source_sha256: Sha256

    @model_validator(mode="after")
    def exact_source(self):
        if source_sha256(self.source) != self.source_sha256:
            raise ValueError("Source extraction hash differs")
        if len({part.member_id for part in self.parts}) != len(self.parts):
            raise ValueError("Duplicate source response part")
        return self


class ObservedGenerationDecision(Contract):
    schema_version: Literal["mineagent.rdagent-adoption-observation.v1"] = (
        "mineagent.rdagent-adoption-observation.v1"
    )
    logical_step: int = Field(strict=True, ge=0, le=7)
    evaluated_source_id: UUID | None
    previous_adopted_source_id: UUID | None
    adopted_source_id: UUID | None
    continuation_source_id: UUID | None
    adopted_new: bool = Field(strict=True)

    @model_validator(mode="after")
    def actual_choice(self):
        if self.adopted_new:
            if (
                self.evaluated_source_id is None
                or self.adopted_source_id != self.evaluated_source_id
            ):
                raise ValueError("New adoption must select the evaluated source")
        elif self.adopted_source_id != self.previous_adopted_source_id:
            raise ValueError("Rejected candidate cannot replace fallback source")
        return self


def research_definition(config, code_sha256):
    """Deterministic declared definition; does not certify implementation semantics."""
    from packages.factors.contracts import FactorDefinition

    return FactorDefinition(
        code_sha256=code_sha256,
        factor_kind=config.task.factor_kind,
        input_fields=config.input.input_fields,
        lookback_sessions=config.input.lookback_sessions,
        lag_sessions=config.input.lag_sessions,
        calculation_universe_policy="frozen_universe"
        if config.input.calculation_universe.universe_version_id is not None
        else "dataset_inventory",
        preprocessing=config.input.preprocessing,
        output_unit=config.task.output_unit,
        interpretation=config.task.interpretation,
    )


def research_snapshot_input(
    config, *, factor_version_id, code_sha256, exposure_history
):
    """Canonical source-specific input, with explicit conservative history supplied."""
    from packages.factors.contracts import FactorVersionRef, SnapshotInput, digest

    definition = research_definition(config, code_sha256)
    return SnapshotInput(
        version=FactorVersionRef(
            factor_version_id=factor_version_id,
            definition_sha256=definition.sha256,
            code_sha256=code_sha256,
        ),
        definition=definition,
        dataset=config.input.dataset,
        calculation_universe=config.input.calculation_universe,
        window=config.input.window,
        market_timezone=config.input.market_timezone,
        calendar_sha256=config.input.calendar_sha256,
        environment_sha256=digest(config.runtime),
        adapter_version=config.runtime.adapter_version,
        materialization_profile="mineagent.rdagent-isolated-prefix.v2",
        exposure_history=exposure_history,
    )
