"""Maintenance head preserves exact capabilities, never infers future authority."""

from types import SimpleNamespace

import pytest

from backend.app.rdagent_snapshot_config import (
    composed_controller_in_session,
    provider_config_in_session,
)


def session(revisions):
    return SimpleNamespace(scalars=lambda sql: SimpleNamespace(all=lambda: revisions))


@pytest.mark.parametrize(
    "revision",
    [
        "027_rd_controller_v3",
        "027a_contract_repairs",
        "027b_provider_material",
        "027c_seeded_controller_v4",
        "028_oe_structured_search",
        "029_research_workflow",
        "030_declared_upload_basket",
    ],
)
def test_exact_maintenance_has_same_composed_capability(revision):
    assert provider_config_in_session(session([revision])) is True
    assert composed_controller_in_session(session([revision])) is True


@pytest.mark.parametrize(
    "revisions",
    [
        [],
        ["027a_unknown"],
        ["028_future"],
        ["027_rd_controller_v3", "027a_contract_repairs"],
    ],
)
def test_unknown_or_multirow_never_inherits_capability(revisions):
    with pytest.raises(ValueError, match="Unsupported historical snapshot revision"):
        composed_controller_in_session(session(revisions))


def test_old_seed_revision_keeps_old_controller_only():
    assert provider_config_in_session(session(["026_rd_seeded_submissions"])) is True
    assert (
        composed_controller_in_session(session(["026_rd_seeded_submissions"])) is False
    )


@pytest.mark.parametrize(
    "revisions",
    [[], ["028_future"], ["027a_contract_repairs", "026_rd_seeded_submissions"]],
)
def test_seed_bootstrap_never_reads_family_under_unknown_or_multirow_head(revisions):
    from backend.app.rdagent_seed_controller import seeded_bootstrap_if_declared

    def forbidden(*args):
        raise AssertionError("No seeded table access before exact revision")

    value = session(revisions)
    value.scalar = forbidden
    assert (
        seeded_bootstrap_if_declared(value, None, binding=None, spec=None, config=None)
        is None
    )


@pytest.mark.parametrize(
    "revision",
    [
        "026_rd_seeded_submissions",
        "027_rd_controller_v3",
        "027a_contract_repairs",
        "027b_provider_material",
        "027c_seeded_controller_v4",
        "028_oe_structured_search",
        "029_research_workflow",
        "030_declared_upload_basket",
    ],
)
def test_seed_bootstrap_exact_head_still_requires_saved_receipt(revision):
    from uuid import uuid4

    from backend.app.rdagent_seed_controller import seeded_bootstrap_if_declared

    value = session([revision])
    value.scalar = lambda query: None
    assert (
        seeded_bootstrap_if_declared(
            value,
            SimpleNamespace(owner_id=uuid4(), run_id=uuid4()),
            binding=None,
            spec=None,
            config=None,
        )
        is None
    )


@pytest.mark.parametrize(
    "revision,workflow,declared",
    [
        ("027_rd_controller_v3", False, False),
        ("027a_contract_repairs", False, False),
        ("028_oe_structured_search", False, False),
        ("029_research_workflow", True, False),
        ("030_declared_upload_basket", True, True),
    ],
)
def test_maintenance_does_not_grant_future_workflow_or_upload(
    revision, workflow, declared
):
    from backend.app.rdagent_snapshot_config import (
        declared_upload_in_session,
        workflow_config_in_session,
    )

    value = session([revision])
    assert workflow_config_in_session(value) is workflow
    assert declared_upload_in_session(value) is declared
