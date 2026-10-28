"""Execute real CI wrappers through bounded PgTools; Docker is a local argv double.

No daemon, PostgreSQL listener, credentials or archive operations are involved.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from backend.app.operations_backup import (
    BackupError,
    BackupLimits,
    PgTools,
    PostgresDatabase,
)

WORKFLOW = Path(".github/workflows/mineagent-structured-factor.yml")


def wrappers(tmp_path, monkeypatch, *, wrong=None):
    workflow = WORKFLOW.read_text()
    start = workflow.split("      - name: Start exact disposable PostgreSQL", 1)[
        1
    ].split("      - name:", 1)[0]
    name = re.search(r"--name ([a-z-]+)", start)[1]
    label = re.search(r"--label io.tradegrad.ci=([a-z-]+)", start)
    label = label[1] if label else "<no value>"
    if wrong == "name":
        name = "unrelated-service"
    elif wrong == "label":
        label = "unrelated-label"
    identity = "a" * 64
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "invocations.jsonl"
    docker = bindir / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        "import json,sys\n"
        f'with open({str(log)!r}, "a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")\n'
        "args=sys.argv[1:]\n"
        'if args[0]=="inspect":\n'
        f'    print({identity!r} if args[2]=="{{{{.Id}}}}" else {("/" + name + " " + label)!r})\n'
        'elif args[0]=="exec" and args[-1]=="--version" and args[-2] in ("pg_dump","pg_restore"):\n'
        '    print(args[-2]+" (PostgreSQL) 18.6")\n'
        "else: raise SystemExit(91)\n"
    )
    docker.chmod(0o700)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    private = tmp_path / "var/numeric-private"
    private.mkdir(parents=True)
    (private / "postgres.cid").write_text(identity)
    script = tmp_path / "script"
    script.mkdir()
    (script / "ci_pg_client.py").write_bytes(
        Path("script/ci_pg_client.py").read_bytes()
    )
    raw = workflow.split("python - <<'PYCLIENT'\n", 1)[1].split(
        "          PYCLIENT", 1
    )[0]
    code = "\n".join(line[10:] for line in raw.splitlines())
    environment_file = tmp_path / "github-env"
    environment = dict(os.environ, GITHUB_ENV=str(environment_file))
    subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=environment,
        check=True,
        timeout=10,
        capture_output=True,
    )
    commands = dict(
        line.split("=", 1) for line in environment_file.read_text().splitlines()
    )
    tools = PgTools(
        pg_dump=(commands["PG_DUMP_EXECUTABLE"],),
        pg_restore=(commands["PG_RESTORE_EXECUTABLE"],),
    )
    return tools, log


@pytest.mark.parametrize("wrong", [None, "name", "label"])
def test_generated_clients_match_exact_disposable_identity_in_clean_backup_environment(
    tmp_path, monkeypatch, wrong
):
    tools, log = wrappers(tmp_path, monkeypatch, wrong=wrong)
    database = PostgresDatabase(
        "postgresql://tradegrad_ci:test-only@127.0.0.1:5432/tradegrad_backup_test_"
        + "b" * 32
    )
    environment = database.environment()
    assert "TRADEGRAD_CI_POSTGRES_ID" not in environment
    assert "GITHUB_ACTIONS" not in environment
    assert "DOCKER_CONFIG" not in environment
    if wrong:
        with pytest.raises(BackupError, match="PostgreSQL client failed"):
            tools.check_versions(environment, 18, BackupLimits())
    else:
        tools.check_versions(environment, 18, BackupLimits())
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    executions = [args for args in calls if args[0] == "exec"]
    assert len(executions) == (0 if wrong else 2)
    assert all(args[-1] == "--version" for args in executions)
    assert all("test-only" not in arg for args in calls for arg in args)


def test_cleanup_and_health_use_same_exact_service_identity():
    text = WORKFLOW.read_text()
    assert "--name tradegrad-ci-postgres " in text
    assert (
        "--label io.mineagent.ci=numeric-postgres --label io.tradegrad.ci=postgres "
        in text
    )
    assert "'{{.State.Health.Status}}' tradegrad-ci-postgres)" in text
    assert 'test "$actual" = "$id|/tradegrad-ci-postgres|numeric-postgres"' in text
    assert "mineagent-numeric-postgres" not in text
