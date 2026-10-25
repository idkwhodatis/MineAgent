"""Integer, bounded run-global resource accounting; no floating-point money."""

from pydantic import BaseModel, ConfigDict, Field

MAX_UNITS = 2**53 - 1


class BudgetVector(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    calls: int = Field(default=0, ge=0, le=MAX_UNITS)
    tokens: int = Field(default=0, ge=0, le=MAX_UNITS)
    cost_microunits: int = Field(default=0, ge=0, le=MAX_UNITS)
    mutation_slots: int = Field(default=0, ge=0, le=MAX_UNITS)
    evaluator_executions: int = Field(default=0, ge=0, le=MAX_UNITS)
    wall_milliseconds: int = Field(default=0, ge=0, le=MAX_UNITS)
    storage_bytes: int = Field(default=0, ge=0, le=MAX_UNITS)
    process_slots: int = Field(default=0, ge=0, le=MAX_UNITS)

    def add(self, other: "BudgetVector") -> "BudgetVector":
        return BudgetVector(
            **{k: getattr(self, k) + getattr(other, k) for k in type(self).model_fields}
        )

    def subtract(self, other: "BudgetVector") -> "BudgetVector":
        return BudgetVector(
            **{k: getattr(self, k) - getattr(other, k) for k in type(self).model_fields}
        )

    def covers(self, other: "BudgetVector") -> bool:
        return all(
            getattr(self, k) >= getattr(other, k) for k in type(self).model_fields
        )


def validate_ledger(limits: dict, used: dict, reserved: dict) -> None:
    cap, spent, held = (
        BudgetVector.model_validate(v) for v in (limits, used, reserved)
    )
    if not cap.covers(spent.add(held)):
        raise ValueError("Budget projection exceeds immutable limits")
