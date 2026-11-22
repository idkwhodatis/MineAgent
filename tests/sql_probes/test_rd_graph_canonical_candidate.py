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
    assert 'position(chr(127) in request_utf8 COLLATE "C")=0' in candidate
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


def test_default_workflow_call_supplies_exact_required_inputs_by_name():
    import ast
    from pathlib import Path

    integration = Path(__file__).parents[1] / "integration"
    workflow = ast.parse(
        (integration / "test_rdagent_workflow_user_flow.py").read_text()
    )
    experiment = ast.parse(
        (integration / "test_rd_graph_canonical_candidate_postgres.py").read_text()
    )
    original = next(
        node
        for node in workflow.body
        if isinstance(node, ast.FunctionDef)
        and node.name
        == "test_normal_workflow_children_consume_frozen_inputs_and_real_numeric_results"
    )
    calls = [
        node
        for node in ast.walk(experiment)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_unchanged_workflow"
    ]
    assert len(calls) == 1 and not calls[0].args
    args = {kw.arg: kw.value for kw in calls[0].keywords}
    assert set(args) == {arg.arg for arg in original.args.args}
    assert isinstance(args["allow_feedback_release"], ast.Constant)
    assert args["allow_feedback_release"].value is False
    assert isinstance(args["pytestconfig"], ast.Name)
    assert args["pytestconfig"].id == "pytestconfig"


def _diagnostic_xml(phase="corpus", ordinal="24"):
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
    ET.SubElement(props, "property", name="private", value="unsafe-xml-property")
    ET.SubElement(case, "failure", message="unsafe-log-message").text = "unsafe-body"
    setup = ET.SubElement(
        suite,
        "testcase",
        name="test_default_off_populated_rd_proposal_adoption_graph_matches_candidate",
    )
    ET.SubElement(setup, "error", message="unsafe-fixture-message")
    return root


def test_canonical_diagnostic_projects_allowlist_only_including_fixture_error():
    results = probe.project_diagnostic_cases(_diagnostic_xml())
    assert results == {
        "test_default_off_populated_rd_proposal_adoption_graph_matches_candidate": (
            "error",
            "fixture",
            None,
        ),
        "test_installed032g_request_encoding_bytes_and_errors": (
            "failure",
            "corpus",
            24,
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
            probe.project_diagnostic_cases(_diagnostic_xml(phase, ordinal))
        assert "unsafe-value" not in str(error.value)
