"""Exact new runtime source closure; no installed Docker acceptance claimed."""

from pathlib import Path
from zipfile import ZipFile

from runtimes.rdagent_operational.build_profile import FILES, build


def test_workflow_operational_wheel_is_deterministic_and_closed(tmp_path):
    root = Path(__file__).resolve().parents[2]
    one = build(root, tmp_path / "one")
    two = build(root, tmp_path / "two")
    assert one == two
    assert len(FILES) == len(set(FILES))
    required = {
        "packages/rdagent_adapter/workflow_config.py",
        "packages/rdagent_adapter/workflow_bootstrap.py",
        "packages/rdagent_adapter/workflow_prompt.py",
        "runtimes/rdagent_operational/workflow_scenario.py",
        "packages/research_workflow/contracts.py",
        "packages/research_workflow/model_inputs.py",
        "packages/research_inputs/contracts.py",
        "packages/research_inputs/resolved.py",
    }
    assert required <= set(FILES)
    with ZipFile(tmp_path / "one" / one["operational"]["filename"]) as archive:
        names = set(archive.namelist())
        assert {p for p in names if ".dist-info/" not in p} == set(FILES)
        for name in required:
            assert archive.read(name) == (root / name).read_bytes()
        assert not any(name.startswith("backend/") for name in names)
        assert "packages/rdagent_provider/transport.py" not in names
        assert "packages/rdagent_provider/operator_profiles.py" not in names
    entrypoint = (root / "runtimes/rdagent_operational/entrypoint.py").read_text()
    assert "bootstrap = parse_workflow_bootstrap(raw)" in entrypoint
    assert entrypoint.index(
        "if type(config) is WorkflowFactorResearchConfig:"
    ) < entrypoint.index("elif isinstance(config, ProviderFactorResearchConfig):")
