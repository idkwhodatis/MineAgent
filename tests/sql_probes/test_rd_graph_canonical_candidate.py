"""Static binding; PostgreSQL and populated workflow evidence are separate."""

from backend.app.row_validation_oe_032g_base_sql import SOURCES
from tests.sql_probes import rd_graph_canonical_candidate as probe


def test_only_ascii_assignment_inside_exact_current_per_operation_graph_changes():
    original = probe.pinned_sources()[probe.GRAPH]
    candidate = probe.candidate_source()
    assert SOURCES[probe.GRAPH] == "029_research_workflow.py"
    assert SOURCES[probe.ENCODER] == "032d_rd_validation_cost.py"
    encoder = probe.pinned_sources()[probe.ENCODER]
    assert "IF current_setting('server_encoding') <> 'UTF8'" in encoder
    assert "ascii_only AND z.p>=127" in encoder

    assert original.count(probe.BASELINE) == candidate.count(probe.CANDIDATE) == 1
    assert candidate.replace(probe.CANDIDATE, probe.BASELINE, 1) == original
    assert original.index(" FOR op IN SELECT") < original.index(probe.BASELINE)
    assert original.index(
        "AND mineagent_rd_provider_payload(op.request"
    ) < original.index(probe.BASELINE)
    assert candidate.index("request_utf8:=") < candidate.index(
        "octet_length(request_utf8)"
    )
    assert 'position(chr(127) in (request_utf8 COLLATE "C"))=0' in candidate
    assert candidate.count("mineagent_rd_canonical_json(op.request,true)") == 1
    assert candidate.index("    ELSE\n     request_ascii:=") < candidate.index(
        "mineagent_rd_canonical_json(op.request,true)"
    )


def test_experiment_emits_only_closed_temp_functions():
    schema = "tradegrad_test_" + "0" * 32
    graph = probe.temp_graph_source(schema)
    assert graph.startswith("CREATE FUNCTION pg_temp.rd_graph_ascii_reuse(rid uuid)")
    assert graph.count("CREATE FUNCTION") == 1 and "CREATE OR REPLACE" not in graph
    for candidate in (False, True):
        value = probe.temp_bytes_source(schema, candidate=candidate)
        assert value.startswith("CREATE FUNCTION pg_temp.")
        assert value.count("CREATE FUNCTION") == 1 and "CREATE OR REPLACE" not in value
        assert (probe.CANDIDATE if candidate else probe.BASELINE) in value


def test_observer_reuses_original_default_terminal_assertions_only():
    from pathlib import Path

    integration = Path(__file__).parents[1] / "integration"
    workflow = (integration / "test_rdagent_workflow_user_flow.py").read_text()
    experiment = (
        integration / "test_rd_graph_canonical_candidate_postgres.py"
    ).read_text()
    assert workflow.count("observe_populated_graph(") == 1
    assert workflow.index("engine.dispose()") < workflow.index(
        "observe_populated_graph("
    )
    assert "if observe_graph and sys.exception() is None:" in workflow
    assert (
        workflow.count('os.environ.get("MINEAGENT_RD_GRAPH_ASCII_PROBE_REQUIRED")') == 1
    )
    assert (
        "observe_graph = (\n"
        '                        os.environ.get("MINEAGENT_RD_GRAPH_ASCII_PROBE_REQUIRED") == "1"\n'
        "                    )\n"
        "                    assert_browser_history(wid)\n"
        "                    assert_restored_history(wid)\n"
        "                    return"
    ) in workflow
    assert (
        "        observe_graph = False\n    finally:\n        engine.dispose()"
        in workflow
    )
    assert "_unchanged_workflow" not in experiment
    assert "def " + probe.GRAPH_CASE not in experiment


def _diagnostic_xml(phase="corpus", ordinal="24", sqlstate=None):
    from xml.etree import ElementTree as ET

    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    case = ET.SubElement(
        suite, "testcase", name="test_installed032g_request_encoding_bytes_and_errors"
    )
    props = ET.SubElement(case, "properties")
    ET.SubElement(props, "property", name="canonical_phase", value=phase)
    if ordinal is not None:
        ET.SubElement(props, "property", name="canonical_ordinal", value=ordinal)
    if sqlstate is not None:
        ET.SubElement(props, "property", name="canonical_sqlstate", value=sqlstate)
    ET.SubElement(props, "property", name="private", value="unsafe-xml-property")
    ET.SubElement(case, "failure", message="unsafe-log-message").text = "unsafe-body"
    return root


def _workflow_xml(*, original=None, phase=None, status=None):
    from xml.etree import ElementTree as ET

    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    other = ET.SubElement(suite, "testcase", name="opt-in")
    ET.SubElement(other, "failure", message="unsafe-other-workflow")
    case = ET.SubElement(
        suite, "testcase", name=probe.WORKFLOW_DEFAULT, classname=probe.WORKFLOW_CLASS
    )
    props = ET.SubElement(case, "properties")
    if phase is not None:
        ET.SubElement(props, "property", name="canonical_phase", value=phase)
    if status is not None:
        ET.SubElement(props, "property", name="canonical_status", value=status)
    if original is not None:
        ET.SubElement(case, original, message="unsafe-fixture-message")
    return root


def test_canonical_diagnostic_projects_allowlist_only_including_fixture_error():
    results = probe.project_diagnostic_cases(
        _diagnostic_xml(), _workflow_xml(original="error")
    )
    assert results == {
        "test_default_off_populated_rd_proposal_adoption_graph_matches_candidate": (
            "unavailable",
            "original_workflow",
            None,
            None,
        ),
        "test_installed032g_request_encoding_bytes_and_errors": (
            "failure",
            "corpus",
            24,
            None,
        ),
    }


def test_canonical_diagnostic_rejects_free_form_phase_or_ordinal():
    import pytest

    for phase, ordinal in (
        ("unsafe-value", None),
        ("fixture", None),
        ("corpus", "25"),
        ("corpus", "01"),
        ("corpus", None),
        ("temp_baseline", "0"),
    ):
        with pytest.raises(ValueError, match="^Unexpected|^Ordinal") as error:
            probe.project_diagnostic_cases(_diagnostic_xml(phase, ordinal), None)
        assert "unsafe-value" not in str(error.value)


def test_ddl_sqlstate_is_closed_and_tied_to_temp_failures():
    import pytest

    name = "test_installed032g_request_encoding_bytes_and_errors"
    for state in ("42601", None, "private", ["42601"]):
        expected = "42601" if state == "42601" else "other"
        assert probe.closed_sqlstate(state) == expected
        result = probe.project_diagnostic_cases(
            _diagnostic_xml("temp_candidate", None, expected), None
        )
        assert result[name] == ("failure", "temp_candidate", None, expected)

    for input_xml in (
        _diagnostic_xml("temp_candidate", None, "private"),
        _diagnostic_xml("corpus", "12", "42601"),
    ):
        with pytest.raises(
            ValueError, match="^Unexpected canonical diagnostic SQLSTATE"
        ) as error:
            probe.project_diagnostic_cases(input_xml, None)
        assert "private" not in str(error.value)


def test_workflow_comparison_verdict_is_independent_and_closed():
    import pytest

    encoder = _diagnostic_xml()
    for root, expected in (
        (None, ("unavailable", "original_workflow", None, None)),
        (_workflow_xml(), ("unavailable", "observer_missing", None, None)),
        (
            _workflow_xml(phase="complete", status="passed"),
            ("passed", "complete", None, None),
        ),
        (
            _workflow_xml(phase="candidate_graph", status="failure"),
            ("failure", "candidate_graph", None, None),
        ),
        *[
            (
                _workflow_xml(original=verdict, phase="complete", status="passed"),
                ("unavailable", "original_workflow", None, None),
            )
            for verdict in ("error", "failure", "skipped")
        ],
    ):
        assert (
            probe.project_diagnostic_cases(encoder, root)[probe.GRAPH_CASE] == expected
        )
    for phase, status in (("private", "failure"), ("complete", "private")):
        with pytest.raises(ValueError, match="^Unexpected"):
            probe.project_diagnostic_cases(
                encoder, _workflow_xml(phase=phase, status=status)
            )


def test_workflow_comparison_rejects_wrong_provenance_or_duplicate():
    import pytest

    encoder = _diagnostic_xml()
    wrong = _workflow_xml(phase="complete", status="passed")
    same_name = next(
        c for c in wrong.iter("testcase") if c.get("name") == probe.WORKFLOW_DEFAULT
    )
    same_name.set("classname", "private")
    with pytest.raises(ValueError, match="^Unexpected original default") as error:
        probe.project_diagnostic_cases(encoder, wrong)
    assert "private" not in str(error.value)
    same_name.set("classname", probe.WORKFLOW_CLASS)
    wrong.find("testsuite").append(same_name)
    with pytest.raises(ValueError, match="^Duplicate original default"):
        probe.project_diagnostic_cases(encoder, wrong)
