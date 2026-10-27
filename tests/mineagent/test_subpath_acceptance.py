"""Pure guard tests are not substitutes for actual hosted Caddy acceptance."""

from pathlib import Path

import pytest

from script.subpath_acceptance import PREFIXES, caddyfile, main, verify_report


def test_root_and_nested_proxy_are_http_only_and_strip_exact_prefix():
    root, nested = map(caddyfile, PREFIXES)
    for config in (root, nested):
        assert "auto_https off" in config
        assert "admin off" in config
        assert "reverse_proxy frontend:8080" in config
    assert "handle_path" not in root
    assert "handle_path /lab/research-7/*" in nested
    assert "redir /lab/research-7 /lab/research-7/ 308" in nested
    assert "respond 404" in nested


@pytest.mark.parametrize("prefix", ["/", "/other", "https://example.com", "/x\n}"])
def test_proxy_config_rejects_unbounded_input(prefix):
    with pytest.raises(ValueError):
        caddyfile(prefix)


def test_real_browser_report_requires_exact_two_cases(tmp_path):
    path = tmp_path / "browser.xml"
    path.write_text(
        '<testsuites><testsuite><testcase name="one"/><testcase name="two"/></testsuite></testsuites>'
    )
    verify_report(path)


@pytest.mark.parametrize(
    "cases",
    [
        "",
        "<testcase/>",
        "<testcase/><testcase/><testcase/>",
        "<testcase><skipped/></testcase><testcase/>",
        "<testcase><failure/></testcase><testcase/>",
        "<testcase><error/></testcase><testcase/>",
    ],
)
def test_missing_extra_failed_or_skipped_browser_cases_fail(tmp_path, cases):
    path = tmp_path / "browser.xml"
    path.write_text(f"<testsuite>{cases}</testsuite>")
    with pytest.raises(ValueError):
        verify_report(path)


def test_no_implicit_docker_execution(monkeypatch):
    monkeypatch.delenv("MINEAGENT_SUBPATH_ACCEPTANCE", raising=False)
    with pytest.raises(SystemExit, match="opt-in"):
        main()


def test_host_connection_override_fails_before_any_execution(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("MINEAGENT_SUBPATH_ACCEPTANCE", "1")
    monkeypatch.setenv("DOCKER_HOST", "tcp://example.invalid")
    with pytest.raises(SystemExit, match="local Docker"):
        main()


def test_harness_has_no_browser_mocks_or_skips():
    source = (
        Path(__file__).parents[2] / "frontend/tests/subpath/reverse-proxy.spec.ts"
    ).read_text()
    assert "page.route(" not in source
    assert "test.skip" not in source
    assert 'test("real ' in source


def test_caddy_file_capability_removed_without_adding_runtime_caps():
    from script.subpath_acceptance import caddy_unprivileged_dockerfile

    image = "caddy@sha256:" + "a" * 64
    assert (
        caddy_unprivileged_dockerfile(image)
        == f"FROM {image}\nRUN setcap -r /usr/bin/caddy\n"
    )
    for bad in [
        "caddy:latest",
        "sha256:short",
        "sha256:" + "a" * 64,
        "other@sha256:" + "a" * 64,
        image + "\nRUN echo bad",
    ]:
        with pytest.raises(ValueError):
            caddy_unprivileged_dockerfile(bad)
    source = (Path(__file__).parents[2] / "script/subpath_acceptance.py").read_text()
    assert '"--cap-add"' not in source
    assert '"no-new-privileges"' in source


def test_browser_failure_outcome_never_releases_values(tmp_path):
    import json
    from script.subpath_acceptance import browser_failure_outcome

    path = tmp_path / "report.xml"
    path.write_text(
        '<testsuite><testcase name="private-user-token"><failure message="secret">'
        "private-response-secret at frontend/tests/subpath/reverse-proxy.spec.ts:41:7"
        "</failure></testcase><testcase/></testsuite>"
    )
    result = browser_failure_outcome(path)
    assert result == {
        "report": "bounded",
        "cases": [
            {"case": 1, "passed": False, "source_frames": [(41, 7)]},
            {"case": 2, "passed": True, "source_frames": []},
        ],
    }
    assert "private" not in json.dumps(result) and "secret" not in json.dumps(result)
    path.write_text("<not-xml")
    assert browser_failure_outcome(path) == {"report": "unavailable"}
    path.write_text("<testsuite>" + "<testcase/>" * 9 + "</testsuite>")
    assert browser_failure_outcome(path) == {"report": "unavailable"}

    path.write_bytes(b'<!DOCTYPE testsuite [<!ENTITY x "private-secret">]><testsuite/>')
    assert browser_failure_outcome(path) == {"report": "unavailable"}
    path.write_bytes(b"x" * 20_000_001)
    assert browser_failure_outcome(path) == {"report": "unavailable"}
    path.write_text(
        "<testsuite><testcase><failure>"
        "tests/subpath/reverse-proxy.spec.ts:0:1 "
        "tests/subpath/reverse-proxy.spec.ts:99999:1 "
        "tests/subpath/reverse-proxy.spec.ts:41:0 "
        "</failure></testcase></testsuite>"
    )
    assert browser_failure_outcome(path)["cases"][0]["source_frames"] == []
