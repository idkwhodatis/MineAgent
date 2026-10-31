"""Exact required hosted gate wiring; static checks cannot prove runtime acceptance."""

from pathlib import Path

WORKFLOW = Path(".github/workflows/mineagent-structured-factor.yml")


def test_required_pair_and_cold_restore_are_exact_cases_without_skip_acceptance():
    text = WORKFLOW.read_text()
    assert 'MINEAGENT_STRUCTURED_ENGINE_REQUIRED: "1"' in text
    assert "tests/integration/test_structured_factor_runtime.py" in text
    assert "assert len(cases)==2" in text
    assert (
        "{'test_actual_two_factor_versions_drive_two_closed_engine_units','test_actual_structured_pair_cold_restore_disclosure_replay'}"
        in text
    )
    assert "('failure','error','skipped')" in text
    assert "twenty actual units across live and independent cold restore cases" in text


def test_measured_unchanged_parent_and_no_network_derivative():
    text = WORKFLOW.read_text()
    assert "python -m packages.tradegrad_adapter.portable_environment" in text
    assert "-f runtimes/portable_engine/Dockerfile ." in text
    assert (
        'docker build --pull=false --network none --build-arg "TRUSTED_ENGINE_IMAGE=$parent_ref"'
        in text
    )
    assert "-f runtimes/structured_factor_engine/Dockerfile ." in text
    assert 'docker tag "$parent" "$parent_ref"' in text
    assert (
        text.count(
            'test "$(docker image inspect --format \'{{.Id}}\' "$parent_ref")" = "$parent"'
        )
        == 4
    )
    assert "docker push" not in text


def test_private_outcomes_and_all_process_classes_checked():
    text = WORKFLOW.read_text()
    assert "var/numeric-private/controller.xml" in text
    assert "summarize(source,Path('var/numeric-proof/controller-outcomes.xml'))" in text
    assert "path: var/numeric-proof/" in text
    for profile in ("structured", "numeric", "analysis"):
        assert f"label=io.mineagent.{profile}.admission" in text
    assert "Confirm exact controller units are absent" in text
    assert "label-based removal" in text


def test_actual_test_uses_original_fixture_pair_and_terminal_history():
    text = Path("tests/integration/test_structured_factor_runtime.py").read_text()
    text += Path("tests/integration/structured_execution_fixture.py").read_text()
    for name in (
        "prepare_structured_pair",
        "execute_structured_acceptance_unit",
        "persist_structured_execution_outcome",
        "read_verified_structured_execution",
        "finish_structured_acceptance_run",
        "read_historical_structured_execution",
    ):
        assert name in text
    assert "shutil.copytree" in text
    assert "assert attempted == []" in text


def test_independent_cold_case_uses_existing_confined_streaming_pg_tools():
    text = WORKFLOW.read_text()
    assert 'RUN_BACKUP_RESTORE_DRILL: "1"' in text
    assert "tests/integration/test_structured_factor_restore.py -q" in text
    assert "script/ci_pg_client.py" in text
    assert "TRADEGRAD_CI_POSTGRES_ID=" in text
    assert "if actual != container:" in text
    assert 'for tool in ("pg_dump", "pg_restore"):' in text
    assert "DOCKER_CONFIG=" in text


def test_genuine_browser_is_required_after_history_without_extra_units():
    workflow = WORKFLOW.read_text()
    assert 'MINEAGENT_STRUCTURED_BROWSER_REQUIRED: "1"' in workflow
    assert "npm ci --prefix frontend" in workflow
    assert "npm run build --prefix frontend" in workflow
    assert "npx playwright install --with-deps chromium" in workflow
    assert "cache-dependency-path: frontend/package-lock.json" in workflow
    assert "# The first existing ten-unit case" in workflow
    source = Path("tests/integration/test_structured_factor_runtime.py").read_text()
    assert "settings, owner, other, tmp_path" in source
    assert source.index(
        'monkeypatch.setattr(StructuredSupervisor, "execute", no_launch)'
    ) < source.index("    assert_actual_structured_result_browser(")
    assert source.count("    assert_actual_structured_result_browser(") == 1
    browser_call = source.split("    assert_actual_structured_result_browser(", 1)[1]
    assert "        restored_settings," in browser_call
    assert source.rstrip().endswith("assert attempted == []")


def test_configured_fixture_initializer_preserves_versioned_revision_gate():
    # Check the actual gate, rather than a parallel list of accepted revisions.
    import ast

    tree = ast.parse(Path("workers/research/rdagent_configured.py").read_text())
    function = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "create_configured_run"
    )
    calls = [
        n
        for n in ast.walk(function)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "require_controller_revision"
    ]
    assert len(calls) == 2  # Source-read transaction and final write transaction.
    gate = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "require_controller_revision"
    )
    revision_gate = next(
        n
        for n in ast.walk(gate)
        if isinstance(n, ast.Compare) and isinstance(n.ops[0], ast.NotIn)
        and isinstance(n.left, ast.Name) and n.left.id == "revisions"
        and isinstance(n.comparators[0], ast.Tuple)
        and ast.literal_eval(n.comparators[0])[0] == ["022_factor_analysis"]
    )
    allowed = ast.literal_eval(revision_gate.comparators[0])
    assert allowed == (
        ["022_factor_analysis"],
        ["023_rd_submissions"],
        ["024_openevolve_initial"],
        ["025_structured_factor_strategy"],
        ["026_rd_seeded_submissions"],
        ["027_rd_controller_v3"],
        ["027a_contract_repairs"],
        ["027b_provider_material"],
        ["027c_seeded_controller_v4"],
        ["028_oe_structured_search"],
        ["029_research_workflow"],
        ["030_declared_upload_basket"],
        ["031_workflow_structured_stage"],
        ["032_normal_oe_note_pins"],
        ["032a_rd_terminal_results"],
    )
    assert ["026_seeded_factor"] not in allowed
    assert ["027_future"] not in allowed


def test_normal_027_requires_exact_browser_and_rejection_cases():
    text = WORKFLOW.read_text()
    for flag in (
        "MINEAGENT_OE_STRUCTURED_NORMAL_REQUIRED",
        "MINEAGENT_OE_NORMAL_BROWSER_REQUIRED",
        "MINEAGENT_RD_REJECTION_FLOW_REQUIRED",
    ):
        assert f'{flag}: "1"' in text
    assert "tests/integration/test_oe_structured_normal_flow.py" in text
    assert "tests/integration/test_rdagent_provider_rejection.py -q" in text
    assert (
        "{'test_normal_demo_generated_factor_and_model_child_reach_actual_engine','test_normal_provider_actual_rejection_preserves_failed_feedback_without_snapshot'}"
        in text
    )
    assert text.count("assert len(cases)==2") == 3
    assert text.count("('failure','error','skipped')") == 6
    assert "var/numeric-private/normal-027.xml" in text
    assert "normal-027-outcomes.xml" in text
    assert "io.mineagent.managed=mineagent-oe-structured-orchestrator-v1" in text


def test_normal_027_builds_pinned_parents_and_isolates_sdk():
    text = WORKFLOW.read_text()
    assert "8c65f74aa906abc0b0e9cae948ddf4c4c6570bdc" in text
    assert (
        "runtimes/openevolve/verify_build_inputs.py .runtime/openevolve-source" in text
    )
    assert "-f runtimes/openevolve_operational/Dockerfile ." in text
    assert (
        "python -m script.build_oe_structured_images --output var/numeric-proof/oe-normal"
        in text
    )
    assert (
        '--controller-parent "$(cat var/numeric-proof/oe-controller-parent.iid)"'
        in text
    )
    assert '--engine-parent "$(cat var/numeric-proof/structured-parent.iid)"' in text
    assert 'python -m venv "$RUNNER_TEMP/rd-provider-sdk"' in text
    assert "--require-hashes -r runtimes/rdagent_provider/requirements.lock" in text
    assert '"$RUNNER_TEMP/rd-provider-sdk/bin/python"' in text
    assert "realpath" not in text
    assert "export MINEAGENT_OE_STRUCTURED_RUNTIME_PROFILE=" in text
    assert "runtimes.rdagent_operational.build_profile" in text
    assert "runtimes/rdagent_operational/wheel-proof.json" in text


def test_normal_027_gate_keeps_old_pair_and_bounded_sequential_budget():
    text = WORKFLOW.read_text()
    assert "timeout-minutes: 120" in text
    assert "timeout-minutes: 40" in text
    old = text.index("Require twenty actual units")
    normal = text.index("Require normal demo RD to OE")
    cleanup = text.index("Confirm exact normal OE controller units")
    assert old < normal < cleanup
    assert text.index("MINEAGENT_OE_STRUCTURED_NORMAL_REQUIRED") > normal
    assert "Seven actual normal-flow Docker units plus two rejection units" in text
    assert 'docker rm --force --volumes "$id"' in text
    assert "docker push" not in text


def test_declared_upload_gate_runs_actual_data_worker_and_both_required_flows():
    text = WORKFLOW.read_text()
    assert 'MINEAGENT_DECLARED_UPLOAD_FLOW_REQUIRED: "1"' in text
    assert "runtimes/data/requirements.lock" in text
    assert "tests/integration/test_declared_upload_research_flow.py -q" in text
    assert (
        "{'test_actual_upload_api_and_data_worker_preserve_declared_originals','test_actual_declared_upload_rd_release_and_normal_structured_oe'}"
        in text
    )
