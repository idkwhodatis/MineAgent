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
