"""Real configured writer/config validation with transaction doubles, not PG."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from test_rdagent_configured_source import retained as retained  # noqa: PLC0414
from test_rdagent_configured_source_service import setup_source as setup_source  # noqa: PLC0414

from backend.app.models import DatasetVersion, ResearchRun, User
from packages.rdagent_adapter.contracts import FactorResearchConfig
from packages.rdagent_adapter.controller_profile import COMPOSED_CONTROLLER_PROFILE
from packages.research_runtime.process_contracts import ProcessError
from workers.research import rdagent_configured as module


@pytest.mark.parametrize(
    "revision",
    [
        "026_rd_seeded_submissions",
        "027_rd_controller_v3",
        "027a_contract_repairs",
        "027b_provider_material",
        "027c_seeded_controller_v4",
    ],
)
@pytest.mark.parametrize("selected", [False, True])
def test_actual_configured_writer_profile_revision_and_frozen_config(
    setup_source, monkeypatch, revision, selected
):
    s = setup_source
    raw = s.config.model_dump(mode="json")
    raw["max_iterations"] = 1
    raw["budget"]["process_slots"] = 4
    raw["budget"]["storage_bytes"] = 512 * 1024**2
    raw["budget"]["wall_milliseconds"] = 300000 if selected else 240000
    if selected:
        raw["controller_profile"] = COMPOSED_CONTROLLER_PROFILE
    config = FactorResearchConfig.model_validate(raw)
    rows = []

    class Session:
        def scalars(self, query):
            return SimpleNamespace(all=lambda: [revision])

        def get(self, model, identity):
            if model is DatasetVersion:
                assert identity == s.dataset.id
                return s.dataset
            assert model is User and identity == s.fence.owner_id
            return object()

        def add(self, row):
            rows.append(row)

        def flush(self):
            pass

    session = Session()

    @contextmanager
    def begin(*args, **kwargs):
        yield session

    factory = SimpleNamespace(begin=begin)
    monkeypatch.setattr(module, "resolve_dataset_preflight", lambda *a, **kw: s.source)
    monkeypatch.setattr(module, "neutral_transaction", begin)
    for name in ("initialize_budget", "bind_research_attempt", "prepare_analysis_plan"):
        monkeypatch.setattr(module, name, lambda *a, **kw: None)
    args = {
        "owner_id": s.fence.owner_id,
        "dataset_id": s.dataset.id,
        "config": config,
        "objective": s.plan.objective,
        "source_policy": s.args["policy"],
        "qlib_installed_parent_image_id": "sha256:" + "e" * 64,
        "settings": s.settings,
    }
    if selected and revision not in (
        "027_rd_controller_v3",
        "027a_contract_repairs",
        "027b_provider_material",
        "027c_seeded_controller_v4",
    ):
        with pytest.raises(ProcessError, match="rd_controller_revision"):
            module.create_configured_run(factory, **args)
        assert rows == []
    else:
        fence = module.create_configured_run(factory, **args)
        run = next(row for row in rows if isinstance(row, ResearchRun))
        assert run.id == fence.run_id
        assert run.config == config.model_dump(mode="json")
        assert run.request_sha256 == config.sha256
        assert ("controller_profile" in run.config) is selected
