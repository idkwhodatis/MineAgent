"""Workflow structure only; actual gate has twenty exact no-skip PostgreSQL cases."""

from pathlib import Path


def test_configured_analysis_gate_is_distinct_exact_and_bounded():
    text = Path(".github/workflows/mineagent-configured-analysis.yml").read_text()
    assert "contents: read" in text and "persist-credentials: false" in text
    assert "timeout-minutes: 120" in text
    assert "assert len(cases)==23" in text
    assert "test_actual_configured_source_snapshot_direct_analysis_generation" in text
    assert "('failure','error','skipped')" in text
    assert (
        "MINEAGENT_QLIB_INSTALLED_IMAGE" in text and "MINEAGENT_ANALYSIS_IMAGE" in text
    )
    assert (
        'docker build --pull=false --network none --build-arg "QLIB_IMAGE=$parent_ref"'
        in text
    )
    assert 'docker tag "$parent" "$parent_ref"' in text
    assert text.count("docker image inspect --format") == 2
    assert "runtimes/qlib/Dockerfile.direct" in text
    assert "54355232463878d2eebb91fe0ee5fa7fa1f5976c" in text
    assert "io.mineagent.analysis.admission" in text
    assert (
        "python -m pytest tests/integration/test_rdagent_configured_analysis.py" in text
    )
    assert "docker push" not in text
    for path in (
        "packages/qlib_adapter/**",
        "packages/research_runtime/analysis*",
        "runtimes/qlib/**",
        "runtimes/research_supervisor/analysis*",
        "workers/research/analysis_process.py",
        "workers/research/rdagent_configured.py",
        "tests/qlib/**",
    ):
        assert f'      - "{path}"' in text


def test_direct_image_context_explicitly_includes_every_copy_source():
    dockerfile = Path("runtimes/qlib/Dockerfile.direct").read_text()
    included = set(
        Path("runtimes/qlib/Dockerfile.direct.dockerignore").read_text().splitlines()
    )
    assert "**" in included
    for line in dockerfile.splitlines():
        if line.startswith("COPY "):
            for source in line.split()[1:-1]:
                assert Path(source).is_file()
                assert "!" + source in included
    assert not any(
        ".runtime" in entry or "**" in entry[1:]
        for entry in included
        if entry.startswith("!")
    )


def test_real_cold_restore_case_is_required_not_relocation_only():
    text = Path(".github/workflows/mineagent-configured-analysis.yml").read_text()
    assert 'RUN_BACKUP_RESTORE_DRILL: "1"' in text
    assert "test_actual_configured_analysis_cold_restore_historical_read" in text
    assert "tests/integration/test_configured_analysis_restore.py" in text
    assert "script/ci_pg_client.py" in text
    assert "--name tradegrad-ci-postgres" in text
    assert "--label io.tradegrad.ci=postgres" in text
    assert "$id|/tradegrad-ci-postgres|numeric-postgres" in text
    assert "var/numeric-private/postgres.cid" in text
    assert "PG_DUMP_EXECUTABLE" not in text  # generated from fixed tool allowlist
    assert 'for tool in ("pg_dump", "pg_restore")' in text


def test_genuine_browser_reuses_pinned_node_and_keeps_required_runtime_cases():
    text = Path(".github/workflows/mineagent-configured-analysis.yml").read_text()
    original = Path(".github/workflows/v1-validation.yml").read_text()
    for required in (
        "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020",
        'node-version: "24"',
        "npm ci --prefix frontend",
        "npm run build --prefix frontend",
        "npx playwright install --with-deps chromium",
    ):
        assert required in text and required in original
    assert 'MINEAGENT_CONFIGURED_BROWSER_REQUIRED: "1"' in text
    assert "assert len(cases)==23" in text
    assert "path: var/numeric-proof/" in text
    assert "frontend/test-results" not in text
    assert (
        "engine-results-browser.xml" not in text
    )  # private harness file, not uploaded


def test_real_http_outbox_task_flow_is_required_without_fixture_shortcut():
    text = Path(".github/workflows/mineagent-configured-analysis.yml").read_text()
    assert 'MINEAGENT_RD_USER_FLOW_REQUIRED: "1"' in text
    assert 'MINEAGENT_RD_CREATE_BROWSER_REQUIRED: "1"' in text
    assert "tests/integration/test_rdagent_user_flow.py" in text
    assert (
        "test_authenticated_rd_queue_four_unit_declared_objective_and_exact_replay"
        in text
    )
    assert "tests/integration/test_rdagent_submission_postgres.py" in text
    assert "test_queued_cancel_is_authenticated_and_cannot_create_attempt" in text


def test_provider_gate_uses_normal_entry_and_exact_existing_sdk_lock_subset():
    workflow = Path(".github/workflows/mineagent-configured-analysis.yml").read_text()
    assert 'MINEAGENT_RD_PROVIDER_FLOW_REQUIRED: "1"' in workflow
    assert (
        "test_normal_provider_generated_price_level_uses_frozen_steer_and_real_units"
        in workflow
    )
    assert "test_provider_request_and_conservative_amount_match_python[" in workflow
    assert "MINEAGENT_PROVIDER_SDK_PYTHON" in workflow
    assert '"tests/rdagent_provider/**"' in workflow
    assert "python -m pytest tests/rdagent_provider -q -p no:cacheprovider" in workflow
    assert "assert len(cases)==137" in workflow
    assert "var/numeric-proof/provider-outcomes.xml" in workflow
    assert (
        workflow.index("Install isolated host SDK")
        < workflow.index(
            "Require provider protocol and actual local SDK containment regressions"
        )
        < workflow.index("Build locked frontend")
    )
    assert (
        "test_actual_identity_callback_precedes_request_and_matches_result" in workflow
    )
    assert "--require-hashes -r runtimes/rdagent_provider/requirements.lock" in workflow
    existing = set(Path("runtimes/rdagent/requirements.lock").read_text().splitlines())
    sdk = [
        line
        for line in Path("runtimes/rdagent_provider/requirements.lock")
        .read_text()
        .splitlines()
        if line and not line.startswith("#")
    ]
    assert len(sdk) == 16 and set(sdk) <= existing
    assert any(line.startswith("openai==2.54.0 ") for line in sdk)
    assert any(line.startswith("httpx==0.28.1 ") for line in sdk)
