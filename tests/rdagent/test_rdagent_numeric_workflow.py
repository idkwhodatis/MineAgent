"""BuildKit cache alone does not supply daemon base-image inspect evidence."""

from pathlib import Path

from runtimes.factor_execution.runtime_contract import BASE_IMAGE


def test_numeric_workflow_pulls_exact_inspected_base_before_builds():
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/mineagent-numeric-runtime.yml").read_text()
    exact_pull = f"        run: docker pull {BASE_IMAGE}\n"
    assert workflow.count(exact_pull) == 1
    pull_offset = workflow.index(exact_pull)
    for role, dockerfile in (
        ("candidate", "factor_execution"),
        ("normalizer", "factor_normalizer"),
    ):
        build = f"docker build --iidfile var/numeric-proof/{role}.iid"
        assert pull_offset < workflow.index(build)
        bases = [
            line.split()[1]
            for line in (root / f"runtimes/{dockerfile}/Dockerfile")
            .read_text()
            .splitlines()
            if line.startswith("FROM ")
        ]
        assert bases and all(base == BASE_IMAGE for base in bases)
    assert "test_rdagent_numeric_workflow.py -q" in workflow
    assert "assert len(cases) == 248" in workflow
