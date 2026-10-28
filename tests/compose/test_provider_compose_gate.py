"""Source and real configuration contracts, not actual Docker acceptance."""

import ast
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace as NS
from uuid import UUID, uuid4

import pytest
import yaml

from script import ci_rd_compose_config as config
from workers.research import rdagent_provider_queue as queue

ROOT = Path(__file__).resolve().parents[2]


def test_new_compose_phase_follows_all_existing_actual_proofs_and_old_pg_cleanup():
    job = yaml.safe_load(
        (ROOT / ".github/workflows/mineagent-configured-analysis.yml").read_text()
    )["jobs"]["configured-direct-analysis"]
    steps = job["steps"]
    names = [step.get("name", "") for step in steps]
    prior = names.index("Require actual configured results browser and cold restore")
    cleanup = names.index("Remove only exact disposable numeric PostgreSQL")
    packaged = names.index(
        "Require actual packaged API outbox worker and localhost SDK provider"
    )
    assert prior < cleanup < packaged
    assert "if" not in steps[packaged] and steps[packaged]["timeout-minutes"] == 40
    assert "assert len(cases)==137" in next(
        step["run"]
        for step in steps
        if step.get("name")
        == "Require provider protocol and actual local SDK containment regressions"
    )
    actual = steps[prior]["run"]
    assert "assert len(cases)==20" in actual
    for path in (
        "test_rdagent_provider_user_flow.py",
        "test_configured_analysis_restore.py",
        "test_rdagent_user_flow.py",
    ):
        assert path in actual


def test_gate_runs_actual_application_subset_and_real_legacy_startup_images():
    shell = (ROOT / "script/ci_rd_provider_compose.sh").read_text()
    assert (
        "--profile images build native-evaluator portable-engine portable-policy"
        in shell
    )
    assert '"${compose[@]}" build api research-worker' in shell
    assert "api research-worker evaluator-reconciler research-dispatcher" in shell
    assert "--env-file" in shell and "infra/compose.providers.yml" in shell
    assert (
        "cp --archive" not in shell
        and "chown" not in shell
        and "--user root" not in shell
    )
    assert 'exec "$container" mkdir -m700 /tmp/mineagent-rd-compose' in shell
    assert 'cp "$fixture/$name" "$container:/tmp/mineagent-rd-compose/$name"' in shell
    assert shell.count('copy_fixture "$api"') == 2 and 'copy_fixture "$worker"' in shell
    assert shell.index("python -m script.ci_compose_cleanup") < shell.index(
        'cp "$TRADEGRAD_STATE_ROOT/normal-compose-provider.json"'
    )
    assert "exec --detach" in shell and "ci_rd_compose_inside.py provider" in shell
    assert "python -m script.ci_compose_cleanup" in shell
    assert "docker rm" not in shell and "--network host" not in shell
    assert (
        "actual_compose_application_subset_not_full_frontend_or_live_provider" in shell
    )


def test_inside_fixture_does_not_dispatch_or_run_a_host_worker():
    source = (ROOT / "script/ci_rd_compose_inside.py").read_text()
    ast.parse(source)
    for forbidden in (
        "dispatch_outbox",
        "create_queue_app",
        "run_worker",
        "TestClient",
        "create_app",
    ):
        assert forbidden not in source
    assert '"http://127.0.0.1:8000/api/v1"' in source
    assert "provider_server(sources=(compose_source(),), port=18765)" in source
    assert "verify_sdk(settings.rdagent_provider_python)" in source
    assert 'row.state == "completed"' in source
    assert "read_historical_execution_snapshot" in source
    assert "[98.0, 102.0, 104.0]" in source
    assert '"rank_ic"] != 1.0' in source
    assert 'replay.get("replayed") is not True' in source
    assert "from None" in source and "print(json.dumps(run()" in source


def test_fixture_default_port_retains_old_host_gate_semantics():
    source = (ROOT / "tests/integration/rd_provider_fixture.py").read_text()
    parsed = ast.parse(source)
    function = next(
        node
        for node in parsed.body
        if isinstance(node, ast.FunctionDef) and node.name == "provider_server"
    )
    assert function.args.kwonlyargs[-1].arg == "port"
    assert ast.literal_eval(function.args.kw_defaults[-1]) == 0
    assert 'ThreadingHTTPServer(("127.0.0.1", port), Handler)' in source


def test_synthetic_private_config_uses_actual_iid_files_and_no_live_activation(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    evidence = tmp_path / "var/numeric-proof"
    evidence.mkdir(parents=True)
    roles = ("orchestrator", "candidate", "normalizer", "analysis", "qlib-installed")
    for index, role in enumerate(roles, 1):
        (evidence / (role + ".iid")).write_text("sha256:" + str(index) * 64)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("TRADEGRAD_STATE_ROOT", str(state))
    owner = str(uuid4())
    path = config.write_config(owner)
    values = dict(line.split("=", 1) for line in path.read_text().splitlines())
    assert path.stat().st_mode & 0o777 == 0o600
    assert values["RDAGENT_LIVE_PROVIDER_ENABLED"] == "false"
    assert values["RDAGENT_SYNTHETIC_PROVIDER_ENABLED"] == "true"
    runtime = json.loads(values["RDAGENT_PROVIDER_RUNTIME_PROFILE"])
    assert runtime["runtime"]["orchestrator_image"] == "sha256:" + "1" * 64
    model = json.loads(values["RDAGENT_PROVIDER_PROFILES"])[0]
    assert model["connection"]["owner_ids"] == [owner]
    assert model["connection"]["base_url"] == "http://127.0.0.1:18765/v1"
    # Exercise the actual dispatcher queue resolver, not just YAML key presence.
    from backend.app.services import DomainError
    from packages.rdagent_provider.operator_profiles import RDOperatorModelProfile

    settings = NS(
        rdagent_research_enabled=True,
        rdagent_provider_runtime_profile=runtime,
        rdagent_provider_python=sys.executable,
        rdagent_provider_profiles=(RDOperatorModelProfile.model_validate(model),),
        rdagent_synthetic_provider_enabled=True,
        rdagent_live_provider_enabled=False,
    )
    run = NS(owner_id=UUID(owner), idempotency_key="retained-key")
    row = NS(plan_id=uuid4())
    plan = NS(qlib_installed_parent_image_id=runtime["qlib_installed_parent_image_id"])
    monkeypatch.setattr(queue, "validate_saved_binding", lambda *a: None)
    monkeypatch.setattr(
        queue, "read_request", lambda row: NS(model_profile_id="compose-local-sdk")
    )
    monkeypatch.setattr(queue, "verify_steer_snapshot", lambda *a: None)
    resolved = queue.resolve_provider_runtime(settings)
    monkeypatch.setenv("MINEAGENT_LOCAL_PROVIDER_TEST_SECRET", "synthetic-local-only")
    _, binding = queue.resolve_provider_declaration(
        settings, run.owner_id, "compose-local-sdk"
    )
    frozen = NS(runtime=resolved.runtime, model_binding=binding, steer_snapshot=None)
    session = NS(get=lambda *a: plan)
    queue.require_provider_queue_binding(
        session, run, row, frozen, settings, read_inputs=False
    )
    monkeypatch.delenv("MINEAGENT_LOCAL_PROVIDER_TEST_SECRET")
    with pytest.raises(DomainError):
        queue.require_provider_queue_binding(
            session, run, row, frozen, settings, read_inputs=False
        )
    with pytest.raises(FileExistsError):
        config.write_config(owner)


def test_nonhosted_configuration_refuses_any_write(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("TRADEGRAD_STATE_ROOT", str(tmp_path))
    with pytest.raises(ValueError, match="hosted"):
        config.write_config(str(uuid4()))
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "listing_status,listing,expected", [(0, "", 0), (0, "native-id", 1), (1, "", 1)]
)
def test_cleanup_listing_distinguishes_empty_from_query_failure(
    listing_status, listing, expected
):
    shell = (ROOT / "script/ci_rd_provider_compose.sh").read_text()
    block = shell.split("  for label in ", 1)[1].split("  done", 1)[0]
    block = "for label in " + block + "done"
    script = (
        """outcome=0
fake_docker() { printf '%s' "$LISTING"; return "$LISTING_STATUS"; }
timeout() { shift; "$@"; }
docker_cli=(fake_docker)
"""
        + block
        + '\nexit "$outcome"\n'
    )
    result = subprocess.run(
        ["bash", "-c", script],
        check=False,
        env={"LISTING": listing, "LISTING_STATUS": str(listing_status)},
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == expected
