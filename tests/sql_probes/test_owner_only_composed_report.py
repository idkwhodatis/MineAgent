"""The hosted projector admits only exact attempts and complete attestation."""

from xml.etree import ElementTree as ET

from script import owner_only_composed_report as report

FORWARD = "test_owner_only_forward_lock_and_expiry"
COMPOSED = "test_actual_current032g_owner_only_workflow_and_cold_restore"


def write(path, name, status=None, *, restore=False, extra=None):
    suite = ET.Element("testsuite")
    case = ET.SubElement(suite, "testcase", name=name, classname=report.CLASS)
    if status is not None:
        ET.SubElement(case, status).text = "PRIVATE test diagnostic"
    properties = ET.SubElement(case, "properties")
    if restore:
        for phase in ("owner_only_bridge", "owner_only_cold_restore"):
            ET.SubElement(properties, "property", name=phase, value="passed")
    if extra:
        ET.SubElement(properties, "property", name=extra, value="PRIVATE")
    ET.ElementTree(suite).write(path)


def test_requires_both_exact_cases_and_restore_attestation(tmp_path):
    path = tmp_path / "test.xml"
    assert report.outcome(path, FORWARD) == "unavailable"
    write(path, FORWARD)
    assert report.outcome(path, FORWARD) == "passed"
    assert report.outcome(path, COMPOSED, require_restore=True) == "unavailable"
    write(path, COMPOSED)
    assert report.outcome(path, COMPOSED, require_restore=True) == "unavailable"
    write(path, COMPOSED, restore=True)
    assert report.outcome(path, COMPOSED, require_restore=True) == "passed"
    write(path, COMPOSED, restore=True, extra="owner_only_cold_restore")
    assert report.outcome(path, COMPOSED, require_restore=True) == "unavailable"
    write(path, COMPOSED, status="error", restore=True)
    assert report.outcome(path, COMPOSED, require_restore=True) == "error"


def test_public_report_contains_only_closed_labels(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(report, "ROOT", tmp_path)
    write(tmp_path / "owner-only-forward.xml", FORWARD)
    write(tmp_path / "owner-only-composed.xml", COMPOSED, "failure", restore=True)
    assert report.main() == 1
    assert capsys.readouterr().out == (
        "RD_OWNER_ONLY_COMPOSED_V1 forward=passed composed=failure\n"
        "RD_OWNER_ONLY_COMPOSED_PHASE_V1 after_restore\n"
    )


def test_closed_phase_never_echoes_properties(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(report, "ROOT", tmp_path)
    write(tmp_path / "owner-only-forward.xml", FORWARD)
    write(tmp_path / "owner-only-composed.xml", COMPOSED, "failure")
    assert report.main() == 1
    assert capsys.readouterr().out.endswith("before_bridge_verified\n")
    write(tmp_path / "owner-only-composed.xml", COMPOSED, "failure", extra="PRIVATE")
    assert report.main() == 1
    assert capsys.readouterr().out.endswith("unavailable\n")
