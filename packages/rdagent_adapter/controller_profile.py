"""Explicit immutable controller profile selection, never launch authority.

Missing selection is the historical 60-second profile. The opt-in composed
profile changes only the controller wall reservation; child unit limits and
model budgets remain independently enforced.
"""

from typing import Literal

from pydantic import Field, model_validator

from packages.factors.contracts import Contract
from packages.rdagent_adapter.controller_limits import (
    COMPOSED_CONTROLLER_PROFILE,
    LEGACY_CONTROLLER_PROFILE,
    SEEDED_CONTROLLER_PROFILE,
    composed_wall_floor,
    controller_wall_milliseconds,
    controller_wall_seconds,
)

__all__ = [
    "COMPOSED_CONTROLLER_PROFILE",
    "LEGACY_CONTROLLER_PROFILE",
    "SEEDED_CONTROLLER_PROFILE",
    "ControllerProfileSelection",
    "composed_wall_floor",
    "controller_profile",
    "controller_selection_fields",
    "controller_wall_milliseconds",
    "controller_wall_seconds",
]


class ControllerProfileSelection(Contract):
    controller_profile: Literal["mineagent.rdagent-orchestrator.v3"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="wrap")
    @classmethod
    def explicit_controller_selection(cls, value, handler):
        # Revalidation must validate copied instances too, while omitted legacy
        # fields remain omitted. Explicit JSON null must never alias omission.
        if isinstance(value, cls):
            if (
                "controller_profile" in value.model_fields_set
                and value.controller_profile is None
            ):
                raise ValueError("Explicit controller profile null is not omission")
            value = value.model_dump(mode="python")
        if (
            isinstance(value, dict)
            and "controller_profile" in value
            and value["controller_profile"] is None
        ):
            raise ValueError("Controller profile null is not a legacy selection")
        return handler(value)


def controller_profile(config):
    value = config.controller_profile
    if value is None:
        return LEGACY_CONTROLLER_PROFILE
    controller_wall_milliseconds(value)
    return value


def controller_selection_fields(config):
    value = controller_profile(config)
    return {} if value == LEGACY_CONTROLLER_PROFILE else {"controller_profile": value}
