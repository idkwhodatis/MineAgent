"""Opt-in disposable Docker/Caddy/browser acceptance; never deploy a real host.

Builds each application image once, then exercises root and a nested mount with
runtime configuration only. All public listeners are loopback HTTP; no ACME,
provider, account settings or forwarded-header trust is configured here.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
PREFIXES = ("", "/lab/research-7")
CASES = 2
LABEL = "io.mineagent.subpath-acceptance"


def caddyfile(prefix: str) -> str:
    if prefix not in PREFIXES:
        raise ValueError("Only closed disposable acceptance prefixes are permitted")
    common = "{\n admin off\n auto_https off\n}\n:8080 {\n"
    if not prefix:
        return common + " reverse_proxy frontend:8080\n}\n"
    return (
        common
        + f" redir {prefix} {prefix}/ 308\n handle_path {prefix}/* {{\n  reverse_proxy frontend:8080\n }}\n handle {{\n  respond 404\n }}\n}}\n"
    )


def caddy_unprivileged_dockerfile(image_ref: str) -> str:
    if (
        re.fullmatch(r"(?:docker\.io/library/)?caddy@sha256:[0-9a-f]{64}", image_ref)
        is None
    ):
        raise ValueError("Require the exact pulled official Caddy repository digest")
    # The official executable has cap_net_bind_service=ep. With cap_drop ALL,
    # Linux can reject that file-capability exec before applying no-new-privs.
    # Port 8080 requires no capability: remove it in a disposable derivative,
    # never add runtime capabilities or modify the Docker daemon/host.
    return f"FROM {image_ref}\nRUN setcap -r /usr/bin/caddy\n"


def verify_report(path: Path) -> None:
    cases = list(ElementTree.parse(path).getroot().iter("testcase"))
    if len(cases) != CASES or any(
        item.find(tag) is not None
        for item in cases
        for tag in ("failure", "error", "skipped")
    ):
        raise ValueError("Every real subpath browser case must pass without skips")


def browser_failure_outcome(path: Path) -> dict:
    """Expose only bounded counters and source coordinates, never browser values."""
    unavailable = {"report": "unavailable"}
    try:
        if path.is_symlink() or not path.is_file():
            return unavailable
        with path.open("rb") as stream:
            content = stream.read(20_000_001)
        if (
            len(content) > 20_000_000
            or b"<!DOCTYPE" in content
            or b"<!ENTITY" in content
        ):
            return unavailable
        cases = list(ElementTree.fromstring(content).iter("testcase"))
        lines = len(
            (ROOT / "frontend/tests/subpath/reverse-proxy.spec.ts")
            .read_text()
            .splitlines()
        )
    except (OSError, ElementTree.ParseError):
        return unavailable
    if len(cases) > 8:
        return unavailable
    outcomes = []
    for index, case in enumerate(cases):
        bad = [case.find(tag) for tag in ("failure", "error", "skipped")]
        present = [item for item in bad if item is not None]
        frames = set()
        for item in present:
            for line, column in re.findall(
                r"(?:frontend/)?tests/subpath/reverse-proxy\.spec\.ts:(\d{1,5}):(\d{1,5})",
                "".join(item.itertext()),
            ):
                if 1 <= int(line) <= lines and 1 <= int(column) <= 10000:
                    frames.add((int(line), int(column)))
        outcomes.append(
            {
                "case": index + 1,
                "passed": not present,
                "source_frames": sorted(frames)[:8],
            }
        )
    return {"report": "bounded", "cases": outcomes}


def main() -> None:
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("MINEAGENT_SUBPATH_ACCEPTANCE") != "1"
    ):
        raise SystemExit("Explicit disposable hosted acceptance opt-in required")
    if any(
        os.environ.get(key)
        for key in (
            "DOCKER_HOST",
            "DOCKER_CONTEXT",
            "DOCKER_TLS_VERIFY",
            "DOCKER_CERT_PATH",
        )
    ):
        raise SystemExit("Only the existing local Docker connection is accepted")
    uid, gid = os.getuid(), os.getgid()
    if uid == 0:
        raise SystemExit("Use the existing non-root runner identity")
    deadline = time.monotonic() + 1800
    run_id = uuid4().hex
    network = f"mineagent-subpath-{run_id}"
    folder = ROOT / "var" / f"subpath-{run_id}"
    folder.mkdir(parents=True, mode=0o700)
    state = folder / "state"
    state.mkdir(mode=0o700)
    storage = state / "storage"
    from backend.app.core.atomic_storage import provision_directory

    provision_directory(storage, durable_parent=state)
    env = dict(os.environ)
    env.update(
        DATABASE_URL=f"postgresql+psycopg://tradegrad:{env['POSTGRES_PASSWORD']}@database:5432/tradegrad",
        STORAGE_ROOT=str(storage),
        TRADEGRAD_STATE_ROOT=str(state),
        TRADEGRAD_UID=str(uid),
        TRADEGRAD_GID=str(gid),
        DATA_PROVIDER_MODE="fixture",
        FACTOR_LIBRARY_ENABLED="true",
        FACTOR_IMPORT_ENABLED="false",
        BOOTSTRAP_EMAIL=env["E2E_EMAIL"],
        BOOTSTRAP_PASSWORD=env["E2E_PASSWORD"],
        BOOTSTRAP_SYNTHETIC="1",
        BOOTSTRAP_E2E="1",
    )
    containers: list[str] = []
    images: dict[str, str] = {}

    def command(*args: str, timeout: float = 300, extra_env=None, cleanup=False) -> str:
        remaining = timeout if cleanup else deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Subpath acceptance absolute budget expired")
        result = subprocess.run(
            args,
            check=False,
            cwd=ROOT,
            env=extra_env or env,
            capture_output=True,
            text=True,
            timeout=min(timeout, remaining),
        )
        if result.returncode:
            # Docker/API output may include private fixture values: never publish it.
            raise RuntimeError(f"Subpath acceptance command failed: {args[0]}")
        return result.stdout.strip()

    def container(*args: str, detached=True, container_env=None) -> str:
        cid = command(
            "docker",
            "create",
            "--label",
            f"{LABEL}={run_id}",
            "--network",
            network,
            "--pids-limit",
            "256",
            "--memory",
            "2g",
            "--cpus",
            "2",
            "--log-opt",
            "max-size=5m",
            "--log-opt",
            "max-file=1",
            *args,
            extra_env=container_env,
        )
        if re.fullmatch(r"[0-9a-f]{64}", cid) is None:
            raise ValueError("Missing exact Docker container identity")
        containers.append(cid)
        command("docker", "start", *(("--attach",) if not detached else ()), cid)
        if not detached:
            observed = json.loads(command("docker", "inspect", cid))[0]
            if observed["State"]["ExitCode"] != 0:
                raise RuntimeError("Disposable application setup failed")
            remove(cid)
        return cid

    def remove(cid: str) -> None:
        observed = json.loads(command("docker", "inspect", cid, cleanup=True))[0]
        if observed["Id"] != cid or observed["Config"]["Labels"].get(LABEL) != run_id:
            raise ValueError("Disposable cleanup identity changed")
        command("docker", "rm", "--force", "--volumes", cid, timeout=30, cleanup=True)
        containers.remove(cid)

    application_env = [
        item
        for key in (
            "DATABASE_URL",
            "SECRET_KEY",
            "STORAGE_ROOT",
            "TRADEGRAD_STATE_ROOT",
            "TRADEGRAD_UID",
            "TRADEGRAD_GID",
            "DATA_PROVIDER_MODE",
            "FACTOR_LIBRARY_ENABLED",
            "FACTOR_IMPORT_ENABLED",
            "BOOTSTRAP_EMAIL",
            "BOOTSTRAP_PASSWORD",
            "BOOTSTRAP_SYNTHETIC",
            "BOOTSTRAP_E2E",
            "SUBPATH",
        )
        for item in ("--env", key)
    ]
    app_options = [
        "--user",
        f"{uid}:{gid}",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=64m,mode=1777",
        "--mount",
        f"type=bind,src={storage},dst={storage}",
        *application_env,
    ]
    report = {
        "head_sha": os.environ["GITHUB_SHA"],
        "prefixes": [],
        "scope": "disposable Caddy strip-prefix HTTP and real application browser acceptance",
    }
    network_created = False
    try:
        command("docker", "network", "create", "--label", f"{LABEL}={run_id}", network)
        network_created = True
        for name, tag in (
            ("caddy", "caddy:2.11.7-alpine"),
            ("postgres", "postgres:18.6"),
            ("docker_cli", "docker:28.3.2-cli"),
        ):
            command("docker", "pull", tag)
            image = json.loads(command("docker", "image", "inspect", tag))[0]
            images[name] = image["Id"]
            if name == "caddy":
                caddy_ref = next(
                    ref
                    for ref in image["RepoDigests"]
                    if re.fullmatch(
                        r"(?:docker\.io/library/)?caddy@sha256:[0-9a-f]{64}", ref
                    )
                )
            if name == "docker_cli":
                cli_ref = next(
                    ref
                    for ref in image["RepoDigests"]
                    if ref.startswith("docker@sha256:")
                )
        caddy_build = folder / "Caddy.Dockerfile"
        caddy_build.write_text(caddy_unprivileged_dockerfile(caddy_ref))
        command(
            "docker",
            "build",
            "--iidfile",
            str(folder / "caddy.iid"),
            "-f",
            str(caddy_build),
            ".",
            timeout=120,
        )
        images["caddy_upstream"] = images["caddy"]
        images["caddy_upstream_ref"] = caddy_ref
        images["caddy"] = (folder / "caddy.iid").read_text().strip()
        command(
            "docker",
            "build",
            "--iidfile",
            str(folder / "api.iid"),
            "--build-arg",
            f"DOCKER_CLI_IMAGE={cli_ref}",
            "--target",
            "api",
            "-f",
            "infra/application.Dockerfile",
            ".",
            timeout=600,
        )
        command(
            "docker",
            "build",
            "--iidfile",
            str(folder / "frontend.iid"),
            "--build-arg",
            f"CADDY_IMAGE={caddy_ref}",
            "-f",
            "infra/frontend.Dockerfile",
            ".",
            timeout=600,
        )
        images.update(
            api=(folder / "api.iid").read_text().strip(),
            frontend=(folder / "frontend.iid").read_text().strip(),
        )
        pg = container(
            "--network-alias",
            "database",
            "--env",
            "POSTGRES_PASSWORD",
            "--env",
            "POSTGRES_USER=tradegrad",
            "--env",
            "POSTGRES_DB=tradegrad",
            "--tmpfs",
            "/var/lib/postgresql:rw,nosuid,size=1g",
            "--health-cmd",
            "pg_isready -h 127.0.0.1 -U tradegrad -d tradegrad",
            "--health-interval",
            "1s",
            "--health-retries",
            "60",
            images["postgres"],
        )
        until = time.monotonic() + 90
        while (
            json.loads(command("docker", "inspect", pg))[0]["State"]["Health"]["Status"]
            != "healthy"
        ):
            if time.monotonic() >= until:
                raise TimeoutError("Disposable PostgreSQL did not become healthy")
            time.sleep(1)
        env["SUBPATH"] = ""
        container(*app_options, images["api"], "migrate", detached=False)
        container(*app_options, images["api"], "bootstrap", detached=False)
        for ordinal, prefix in enumerate(PREFIXES):
            env["SUBPATH"] = prefix
            api = container(
                "--network-alias", "api", *app_options, images["api"], "run", "api"
            )
            front = container(
                "--network-alias",
                "frontend",
                "--user",
                "65532:65532",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,size=64m,mode=1777",
                "--env",
                "SUBPATH",
                images["frontend"],
            )
            config = folder / f"caddy-{ordinal}"
            config.mkdir(mode=0o755)
            (config / "Caddyfile").write_text(caddyfile(prefix))
            proxy = container(
                "--publish",
                "127.0.0.1:19080:8080",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--tmpfs",
                "/data:rw,nosuid,nodev,size=4m",
                "--tmpfs",
                "/config:rw,nosuid,nodev,size=4m",
                "--mount",
                f"type=bind,src={config},dst=/etc/caddy,readonly",
                images["caddy"],
            )
            base = "http://127.0.0.1:19080" + prefix
            opener = build_opener(ProxyHandler({}))
            until = time.monotonic() + 60
            while True:
                try:
                    with opener.open(
                        base + "/api/v1/health/ready", timeout=2
                    ) as response:
                        assert json.loads(response.read(65537))["status"] == "ready"
                    break
                except (OSError, ValueError, KeyError, AssertionError):
                    if time.monotonic() >= until:
                        raise TimeoutError(
                            "Caddy/application readiness failed"
                        ) from None
                    time.sleep(1)
            browser_env = {
                **env,
                "E2E_API_URL": base,
                "E2E_SUBPATH": prefix,
                "E2E_SUBPATH_ORIGIN": "http://127.0.0.1:19080",
                "E2E_SUBPATH_REPORT": str(folder / f"browser-{ordinal}.xml"),
            }
            try:
                command(
                    "frontend/node_modules/.bin/playwright",
                    "test",
                    "--config",
                    "frontend/playwright.subpath.config.ts",
                    timeout=240,
                    extra_env=browser_env,
                )
            except (RuntimeError, subprocess.TimeoutExpired):
                print(
                    "SUBPATH_OUTCOMES:"
                    + json.dumps(
                        browser_failure_outcome(Path(browser_env["E2E_SUBPATH_REPORT"]))
                    ),
                    flush=True,
                )
                raise
            verify_report(Path(browser_env["E2E_SUBPATH_REPORT"]))
            report["prefixes"].append(
                {"prefix": prefix, "browser_cases": CASES, "success": True}
            )
            for cid in (proxy, front, api):
                remove(cid)
        report["images"] = images
        (ROOT / "var" / "subpath-acceptance.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
    finally:
        # Cleanup is limited to the exact IDs and label created by this invocation.
        for cid in list(reversed(containers)):
            remove(cid)
        if network_created:
            observed = json.loads(
                command("docker", "network", "inspect", network, cleanup=True)
            )[0]
            if observed["Labels"].get(LABEL) != run_id:
                raise ValueError("Disposable network identity changed")
            command("docker", "network", "rm", network, timeout=30, cleanup=True)


if __name__ == "__main__":
    main()
