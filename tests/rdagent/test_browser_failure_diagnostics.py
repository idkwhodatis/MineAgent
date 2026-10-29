"""Bounded, constant-only browser diagnostics; no browser/E2E acceptance claim."""

from types import SimpleNamespace

import pytest

from script.ci_process_owner import OwnedCommandFailed
from tests.integration import rd_submission_browser as browser

SECRET = "private-http-env-request-credential-path"


def xml(body, name=browser._BROWSER_CASE):
    return f'<testsuites><testsuite><testcase name="{name}">{body}</testcase></testsuite></testsuites>'


@pytest.mark.parametrize(
    "body,expected",
    [
        (
            f'<failure type="{SECRET}" message="{SECRET}">{SECRET}</failure>',
            "expected_case_failure",
        ),
        (
            f'<error type="{SECRET}" message="{SECRET}">{SECRET}</error>',
            "expected_case_error",
        ),
        ("<failure/><error/>", "expected_case_failure_and_error"),
        ("<skipped/>", "diagnostic_unavailable"),
        ("", "diagnostic_unavailable"),
    ],
)
def test_only_fixed_case_tag_categories_escape(tmp_path, capsys, body, expected):
    (tmp_path / "engine-create-browser.xml").write_text(xml(body))
    original = OwnedCommandFailed(SECRET)
    calls = []

    def fail(*args, **kwargs):
        calls.append((args, kwargs))
        raise original

    with pytest.raises(OwnedCommandFailed) as caught:
        browser._run_browser(
            SimpleNamespace(run=fail),
            ["node"],
            seconds=120,
            diagnostic_directory=tmp_path,
        )
    assert caught.value is original
    assert calls == [((["node"],), {"seconds": 120})]
    assert (
        capsys.readouterr().out
        == f"RD browser phase=browser_exit category={expected}\n"
    )


@pytest.mark.parametrize(
    "payload",
    [
        xml("<failure/>", SECRET),
        "<testsuites>" + xml("<failure/>") + xml("<error/>") + "</testsuites>",
        '<!DOCTYPE a [<!ENTITY leak SYSTEM "file:///private">]>'
        + xml("<failure>&leak;</failure>"),
        '<!DOCTYPE a [<!ENTITY a "x"><!ENTITY b "&a;&a;">]>'
        + xml("<failure>&b;</failure>"),
        SECRET,
        xml("<failure/>") + SECRET,
        "\x00" + xml("<failure/>"),
        SECRET * browser._DIAGNOSTIC_LIMIT,
    ],
)
def test_untrusted_or_unbounded_xml_is_not_diagnostic(tmp_path, payload):
    (tmp_path / "engine-create-browser.xml").write_text(payload)
    assert browser._browser_failure_category(tmp_path) == "diagnostic_unavailable"


@pytest.mark.parametrize(
    "kind", ["file_link", "directory_link", "directory", "fifo", "missing"]
)
def test_no_symlink_or_special_file_reads(tmp_path, kind):
    target = tmp_path / "engine-create-browser.xml"
    directory = tmp_path
    if kind == "file_link":
        retained = tmp_path / "retained.xml"
        retained.write_text(xml("<failure/>"))
        target.symlink_to(retained)
    elif kind == "directory_link":
        target.write_text(xml("<failure/>"))
        directory = tmp_path / "linked"
        directory.symlink_to(tmp_path, target_is_directory=True)
    elif kind == "directory":
        target.mkdir()
    elif kind == "fifo":
        browser.os.mkfifo(target)
    assert browser._browser_failure_category(directory) == "diagnostic_unavailable"


def test_oversize_rejected_before_read(tmp_path, monkeypatch):
    (tmp_path / "engine-create-browser.xml").write_bytes(
        b"x" * (browser._DIAGNOSTIC_LIMIT + 1)
    )
    monkeypatch.setattr(browser.os, "read", lambda *args: pytest.fail("oversize read"))
    assert browser._browser_failure_category(tmp_path) == "diagnostic_unavailable"


def test_bounded_read_and_mutation_fail_closed(tmp_path, monkeypatch):
    raw = xml("<failure/>").encode()
    (tmp_path / "engine-create-browser.xml").write_bytes(raw)
    reads = []

    def changed(fd, count):
        reads.append(count)
        return raw + b"x"

    monkeypatch.setattr(browser.os, "read", changed)
    assert browser._browser_failure_category(tmp_path) == "diagnostic_unavailable"
    assert reads == [browser._DIAGNOSTIC_LIMIT + 1]


def test_diagnostic_error_never_replaces_original(tmp_path, monkeypatch):
    original = OwnedCommandFailed()

    def fail(*args, **kwargs):
        raise original

    monkeypatch.setattr(browser, "_browser_failure_category", fail)
    with pytest.raises(OwnedCommandFailed) as caught:
        browser._run_browser(SimpleNamespace(run=fail), diagnostic_directory=tmp_path)
    assert caught.value is original


def test_success_does_not_read_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setattr(
        browser,
        "_browser_failure_category",
        lambda *args: pytest.fail("success reads diagnostic"),
    )
    assert (
        browser._run_browser(
            SimpleNamespace(run=lambda: 7), diagnostic_directory=tmp_path
        )
        == 7
    )


def test_unrelated_failure_is_not_intercepted(tmp_path, capsys):
    original = TimeoutError("deadline")

    def fail():
        raise original

    with pytest.raises(TimeoutError) as caught:
        browser._run_browser(SimpleNamespace(run=fail), diagnostic_directory=tmp_path)
    assert caught.value is original
    assert capsys.readouterr().out == ""
