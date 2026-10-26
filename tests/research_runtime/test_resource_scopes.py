from uuid import uuid4

import pytest

from packages.research_runtime.resource_scopes import (
    SCOPES,
    advisory_lock_plan,
    scope_supplies,
)


@pytest.mark.parametrize("scope", ["objects", "processes"])
def test_existing_lock_order_unchanged(scope):
    owner = uuid4()
    assert advisory_lock_plan(owner, scope) == (
        f"submit:{owner}",
        "research-physical-quota",
        f"research-publish:{owner}",
    )
    assert advisory_lock_plan(owner, "none") == (f"submit:{owner}",)


def test_both_global_quota_locks_precede_publish_and_row_locks():
    owner = uuid4()
    assert advisory_lock_plan(owner, "factor_objects") == (
        f"submit:{owner}",
        "factor-physical-quota",
        "research-physical-quota",
        f"factor-publish:{owner}",
        f"research-publish:{owner}",
    )


@pytest.mark.parametrize("actual", sorted(SCOPES))
@pytest.mark.parametrize("required", sorted(SCOPES))
def test_only_explicit_object_capability_is_supplied(actual, required):
    assert scope_supplies(actual, required) == (
        actual == required
        or (actual, required) == ("factor_objects", "objects")
        or (
            actual == "factor_numeric"
            and required in {"factor_objects", "objects", "processes"}
        )
    )


@pytest.mark.parametrize("bad", [None, True, 1, "factor", "", [], {}])
def test_unknown_scope_never_grants_authority(bad):
    assert not scope_supplies(bad, "objects")
    assert not scope_supplies("factor_objects", bad)
    with pytest.raises(ValueError):
        advisory_lock_plan(uuid4(), bad)


def test_owner_must_be_parsed_uuid():
    with pytest.raises(ValueError):
        advisory_lock_plan(str(uuid4()), "factor_objects")


def test_numeric_combination_is_explicit_not_an_old_scope_escalation():
    owner = uuid4()
    assert advisory_lock_plan(owner, "factor_numeric") == advisory_lock_plan(
        owner, "factor_objects"
    )
    assert not scope_supplies("factor_objects", "processes")
    assert not scope_supplies("processes", "factor_objects")
    assert not scope_supplies("objects", "factor_numeric")
    assert not scope_supplies("factor_objects", "factor_numeric")
