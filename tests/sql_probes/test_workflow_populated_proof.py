"""Source contract only; no claim of completed graphs or native acceptance.

The independent job repeats the real finite-workflow gate without depending on
an earlier unrelated acceptance step. Its source equality is not runtime proof.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ORIGINAL = ROOT / ".github/workflows/mineagent-structured-factor.yml"
INDEPENDENT = ROOT / ".github/workflows/mineagent-workflow-populated-proof.yml"
FIRST_GATE = (
    "Require twenty actual units across live and independent cold restore cases"
)
TARGET = "Require actual finite workflow RD to OE stage and complete restore"
CLEANUP = "Confirm exact normal OE controller units are absent"
OLD_ARTIFACT = "mineagent-structured-factor-${{ github.sha }}"
NEW_ARTIFACT = "mineagent-workflow-populated-baseline-${{ github.sha }}"


def steps(source):
    body = source.split("    steps:\n", 1)[1]
    parts = re.split(r"(?=^      - )", body, flags=re.MULTILINE)
    assert not parts[0].strip()
    return parts[1:]


def named(items, name):
    matches = [
        i for i, item in enumerate(items) if item.startswith(f"      - name: {name}\n")
    ]
    assert len(matches) == 1, f"Missing or repeated step: {name}"
    return matches[0]


def test_exact_original_setup_target_and_cleanup_are_preserved():
    original, independent = steps(ORIGINAL.read_text()), steps(INDEPENDENT.read_text())
    setup_end = named(original, FIRST_GATE)
    target = named(original, TARGET)
    cleanup_start = named(original, CLEANUP)
    assert setup_end == 22 and cleanup_start > target
    expected = original[:setup_end] + [original[target]] + original[cleanup_start:]
    expected[-1] = expected[-1].replace(OLD_ARTIFACT, NEW_ARTIFACT)
    # One additional source-only guard runs after the unchanged host installation.
    source_guard = (
        "      - name: Require independent baseline source contract\n"
        "        run: python -m pytest tests/sql_probes/test_workflow_populated_proof.py -q\n"
    )
    install = named(expected, "Install unchanged host test closure")
    expected.insert(install + 1, source_guard)
    # Full step bytes, including command, env, timeout, if and artifact policy.
    assert independent == expected
    assert len(independent) == 32
    assert named(independent, TARGET) == setup_end + 1


def test_job_environment_runner_and_deadlines_are_unchanged():
    def job_header(source):
        header = source.split("jobs:\n", 1)[1].split("    steps:\n", 1)[0]
        # Only job identifier and explanatory comments differ.
        return [
            line
            for line in header.splitlines()[1:]
            if not line.lstrip().startswith("#")
        ]

    assert job_header(INDEPENDENT.read_text()) == job_header(ORIGINAL.read_text())
    source = INDEPENDENT.read_text()
    assert "    timeout-minutes: 120\n" in source
    target = steps(source)[named(steps(source), TARGET)]
    assert "        timeout-minutes: 40\n" in target
    assert "test_workflow_structured_stage_flow.py" in target
    assert "assert len(cases)==1" in target
    assert (
        "test_actual_final_rd_checkpoint_drives_one_oe_stage_and_exact031_restore"
        in target
    )
    assert "('failure','error','skipped')" in target
    assert 'test "$status" = 0' in target
    assert "        if:" not in target


def test_independent_baseline_does_not_activate_candidate_or_weaken_security():
    source = INDEPENDENT.read_text()
    for required in (
        "independent populated workflow baseline",
        "not full production acceptance",
        "persist-credentials: false",
        "contents: read",
        'E2E_LIVE_PROVIDERS: "0"',
        'MODEL_PROFILES_ENABLED: "false"',
        "--require-hashes",
        "--publish 127.0.0.1:5432:5432",
        "Generate masked disposable database credentials",
        "path: var/numeric-proof/",
        "if-no-files-found: error",
        NEW_ARTIFACT,
    ):
        assert required in source
    for forbidden in (
        "continue-on-error",
        "pull_request_target",
        "secrets.",
        "docker push",
        "oe_trigger_target_probe",
        "render_candidate_sql",
        "DISABLE TRIGGER",
        "fsync=off",
        "synchronous_commit=off",
        "full_page_writes=off",
        "--deselect",
        "--ignore",
        "--maxfail",
        " -k ",
        "path: var/numeric-private",
        "contents: write",
        "actions: write",
    ):
        assert forbidden not in source
    assert source.count("if: always()") == 8
    assert "name: " + OLD_ARTIFACT not in source


def test_baseline_trigger_paths_cover_proof_and_relevant_inputs():
    source = INDEPENDENT.read_text()
    trigger = source.split("permissions:\n", 1)[0]
    assert "  workflow_dispatch:\n" in trigger
    assert "    branches: [master]\n" in trigger
    paths = re.findall(r'^      - "([^"]+)"$', trigger, flags=re.MULTILINE)
    assert paths == [
        ".github/workflows/mineagent-workflow-populated-proof.yml",
        ".github/workflows/mineagent-structured-factor.yml",
        "tests/sql_probes/test_workflow_populated_proof.py",
        "backend/alembic/**",
        "backend/app/*sql.py",
        "tests/integration/*workflow*",
        "tests/integration/oe_structured*",
        "tests/integration/rd_configured*",
        "script/ci_pg_client.py",
    ]
