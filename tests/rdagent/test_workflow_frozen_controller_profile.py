"""Frozen workflow controller selection; no provider process or DB acceptance."""

import sys
from types import SimpleNamespace

import pytest
from test_engine_run_preflight import request_body as request_body  # noqa: PLC0414
from test_rdagent_configured_source import retained as retained  # noqa: PLC0414
from test_rdagent_configured_source_service import setup_source as setup_source  # noqa: PLC0414
from test_rdagent_provider_saved_binding import provider_saved as provider_saved  # noqa: PLC0414
from test_rdagent_submission_receipts import replay as replay  # noqa: PLC0414

from backend.app.rdagent_provider_declaration import settings_for_frozen_controller
from backend.app.rdagent_provider_profile import (
    RDProviderServerRuntimeProfile,
    resolve_provider_runtime,
)
from backend.app.services import DomainError
from packages.factors.contracts import canonical_bytes
from packages.rdagent_adapter.controller_profile import COMPOSED_CONTROLLER_PROFILE
from packages.rdagent_adapter.provider_config import ProviderFactorResearchConfig


@pytest.mark.parametrize(
    "frozen_new,current_new",
    [(False, True), (True, False), (True, True), (False, False)],
)
def test_frozen_selection_not_current_setting_and_no_activation(
    provider_saved, frozen_new, current_new
):
    raw = dict(provider_saved.saved.run.config)
    raw["max_iterations"] = 1
    if frozen_new:
        raw["controller_profile"] = COMPOSED_CONTROLLER_PROFILE
        raw["budget"] = {
            **raw["budget"],
            "wall_milliseconds": 300000
            + 32 * raw["model_binding"]["request_deadline_ms"],
        }
    template = ProviderFactorResearchConfig.model_validate(raw)
    before = canonical_bytes(template)
    current = RDProviderServerRuntimeProfile(
        runtime=template.runtime,
        qlib_installed_parent_image_id="sha256:" + "e" * 64,
        **({"controller_profile": COMPOSED_CONTROLLER_PROFILE} if current_new else {}),
    )
    settings = SimpleNamespace(
        rdagent_research_enabled=True,
        rdagent_provider_runtime_profile=current,
        rdagent_provider_python=sys.executable,
        rdagent_live_provider_enabled=False,
        marker=object(),
    )
    selected = settings_for_frozen_controller(settings, template)
    result = resolve_provider_runtime(selected)
    assert result.controller_profile == template.controller_profile
    assert result.runtime == current.runtime
    assert selected.rdagent_live_provider_enabled is False
    assert selected.marker is settings.marker
    assert settings.rdagent_provider_runtime_profile is current
    assert canonical_bytes(template) == before
    settings.rdagent_research_enabled = False
    with pytest.raises(DomainError):
        resolve_provider_runtime(selected)


@pytest.mark.parametrize("field", ["workflow_prompt_layout", "controller_profile"])
def test_workflow_instance_cannot_hide_explicit_null_selector(field):
    from test_rdagent_workflow_child_contracts import config

    from packages.rdagent_adapter.workflow_config import WorkflowFactorResearchConfig

    invalid = config().model_copy(update={field: None})
    with pytest.raises(ValueError):
        WorkflowFactorResearchConfig.model_validate(invalid)


def test_workflow_snapshot_requires_independent_composed_controller_dispatch():
    from test_rdagent_workflow_child_contracts import config

    from backend.app.rdagent_snapshot_config import parse_snapshot_config
    from packages.rdagent_adapter.workflow_config import WorkflowFactorResearchConfig

    old = config()
    raw = old.model_dump(mode="python")
    raw["controller_profile"] = COMPOSED_CONTROLLER_PROFILE
    raw["budget"] = raw["budget"] | {
        "wall_milliseconds": 300000 + 32 * old.model_binding.request_deadline_ms
    }
    new = WorkflowFactorResearchConfig.model_validate(raw)
    args = {
        "owner_id": new.workflow_inputs.binding.owner_id,
        "run_id": new.workflow_child_run_id,
        "workflow_config": True,
    }
    with pytest.raises(ValueError, match="Composed controller"):
        parse_snapshot_config(new.model_dump(mode="json"), **args)
    assert (
        parse_snapshot_config(
            new.model_dump(mode="json"), composed_controller=True, **args
        )
        == new
    )
    assert parse_snapshot_config(old.model_dump(mode="json"), **args) == old


def test_workflow_template_and_child_reject_manual_seed_v4():
    from test_rdagent_workflow_child_contracts import config

    from packages.rdagent_adapter.workflow_config import WorkflowFactorResearchConfig
    from packages.research_workflow.provider_config import (
        WorkflowProviderResearchTemplate,
    )

    child = config()
    raw = child.model_dump(mode="json")
    raw["controller_profile"] = "mineagent.rdagent-orchestrator.v4"
    raw["budget"]["wall_milliseconds"] = (
        360000 + 32 * child.model_binding.request_deadline_ms
    )
    with pytest.raises(ValueError):
        WorkflowFactorResearchConfig.model_validate(raw)
    template = {k: v for k, v in raw.items() if not k.startswith("workflow_")}
    template["schema_version"] = "mineagent.factor-research.v2"
    template["model_profile"] = "configured.rdagent.v1"
    with pytest.raises(ValueError):
        WorkflowProviderResearchTemplate.model_validate(template)
