"""Closed transaction lock plans, not execution or publication authority.

Combined factor/research publication takes both global quota locks before either
per-owner publication lock or Run/Attempt row. Existing single-family callers
never acquire the other family's locks later. Filesystem admission and all SQL
advisory acquisitions remain nonblocking. An external writer may retain its
filesystem guard across fresh short transactions (including a lost final fence),
but must reacquire the complete SQL plan before metadata mutation. A failed
admission rolls back the whole owner transaction, never just a helper reservation.
"""

from uuid import UUID

SCOPES = frozenset({"none", "objects", "processes", "factor_objects", "factor_numeric"})


def scope_supplies(actual: str, required: str) -> bool:
    if type(actual) is not str or type(required) is not str:
        return False
    if actual not in SCOPES or required not in SCOPES:
        return False
    return (
        actual == required
        or (actual == "factor_objects" and required == "objects")
        or (
            actual == "factor_numeric"
            and required in {"factor_objects", "objects", "processes"}
        )
    )


def advisory_lock_plan(owner_id: UUID, scope: str) -> tuple[str, ...]:
    if type(owner_id) is not UUID or type(scope) is not str or scope not in SCOPES:
        raise ValueError("Invalid neutral resource scope")
    submit = (f"submit:{owner_id}",)
    if scope == "none":
        return submit
    if scope in {"factor_objects", "factor_numeric"}:
        return submit + (
            "factor-physical-quota",
            "research-physical-quota",
            f"factor-publish:{owner_id}",
            f"research-publish:{owner_id}",
        )
    return submit + ("research-physical-quota", f"research-publish:{owner_id}")
