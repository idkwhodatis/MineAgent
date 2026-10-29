"""Explicit live result dispatch; physical rejection proof remains mandatory."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import rdagent_numeric_failure_services as service
from backend.app import rdagent_workflow_boundary as boundary
from backend.app.rdagent_numeric_failure_handler import (
    ProviderRejectionHandler,
    select_provider_result_handler,
)
from backend.app.rdagent_provider_handler import ProviderHandler
from packages.rdagent_adapter.provider_config import parse_research_config
from runtimes.rdagent_operational.rpc import RPCError
from tests.rdagent.test_rdagent_workflow_child_contracts import (
    config as workflow_config,
)
from tests.research_workflow.test_plan import plan


@pytest.mark.parametrize(
    "version,revision,expected",
    [
        (2, "026_rd_seeded_submissions", ProviderHandler),
        (2, "027_rd_controller_v3", ProviderHandler),
        (2, "027a_contract_repairs", ProviderHandler),
        (3, "027a_contract_repairs", None),
        (2, "027b_provider_material", ProviderHandler),
        (2, "027c_seeded_controller_v4", ProviderHandler),
        (3, "027c_seeded_controller_v4", None),
        (3, "027b_provider_material", None),
        (2, "030_declared_upload_basket", ProviderRejectionHandler),
        (3, "030_declared_upload_basket", ProviderRejectionHandler),
        (3, "027_rd_controller_v3", None),
        (2, "028_oe_structured_search", ProviderRejectionHandler),
        (2, "029_research_workflow", ProviderRejectionHandler),
        (3, "029_research_workflow", ProviderRejectionHandler),
        (3, "026_rd_seeded_submissions", None),
        (3, "028_oe_structured_search", None),
        (2, "029_unknown", None),
        (3, "029_unknown", None),
    ],
)
def test_normal_result_dispatch_keeps_exact_installed_families(
    version, revision, expected
):
    config = (
        parse_research_config(plan().research_template)
        if version == 2
        else workflow_config()
    )
    if expected is None:
        with pytest.raises(RPCError):
            select_provider_result_handler(config, [revision])
    else:
        assert select_provider_result_handler(config, [revision]) is expected
    with pytest.raises(RPCError):
        select_provider_result_handler(config, [revision, revision])


@pytest.mark.parametrize(
    "change",
    [None, "missing_receipt", "foreign_owner", "old_revision", "missing_capability"],
)
@pytest.mark.parametrize(
    "revision",
    [
        "029_research_workflow",
        "030_declared_upload_basket",
        "031_workflow_structured_stage",
        "032_normal_oe_note_pins",
    ],
)
def test_actual_failure_gate_requires_workflow_binding_and_numeric_capability(
    monkeypatch, change, revision
):
    config = workflow_config()
    fence = NS(
        owner_id=config.workflow_inputs.binding.owner_id,
        run_id=config.workflow_child_run_id,
    )
    run = NS(
        id=fence.run_id,
        owner_id=uuid4() if change == "foreign_owner" else fence.owner_id,
    )
    session = NS(
        get=lambda *a: run,
        scalars=lambda *a: NS(
            all=lambda: [
                "028_oe_structured_search" if change == "old_revision" else revision
            ]
        ),
    )
    launch = object()
    calls = []

    def capability(*a):
        calls.append("capability")
        if change == "missing_capability":
            raise RPCError("not_installed")

    monkeypatch.setattr(service, "require_numeric_rejection_schema", capability)
    monkeypatch.setattr(service, "require_rd_controller", lambda *a: launch)
    monkeypatch.setattr(service, "require_research_spec", lambda *a: (object(), config))

    def receipt(*a):
        calls.append("immutable_receipt")
        return None if change == "missing_receipt" else uuid4()

    monkeypatch.setattr(boundary, "workflow_boundary_id", receipt)
    if change:
        with pytest.raises(RPCError):
            service.require_failure_profile(session, fence, object())
    else:
        assert service.require_failure_profile(session, fence, object()) is launch
        assert calls == ["capability", "immutable_receipt"]
    if change == "missing_capability":
        assert calls == ["capability"]
