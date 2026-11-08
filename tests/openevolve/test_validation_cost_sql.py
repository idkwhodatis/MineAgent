"""Exact reversible edits: no dropped freshness, graph, or reverse predicates."""

from backend.app import oe_validation_cost_sql as cost


def test_provider_reuse_reconstructs_latest_workflow_predecessor():
    sql = cost.provider_sql()
    expression = "@SCHEMA@.mineagent_rd_canonical_json(request,true)"
    assert sql.count(expression) == 1
    restored = sql.replace(
        "kinds text[]; raw text; config_raw text; request_raw text;",
        "kinds text[]; raw text; config_raw text;",
    ).replace(
        " THEN RAISE EXCEPTION 'OE provider exact request'; END IF;\n"
        f" request_raw:={expression};\n"
        " IF o.request_sha256 IS DISTINCT FROM encode(sha256(convert_to(request_raw,",
        " OR o.request_sha256 IS DISTINCT FROM encode(sha256(convert_to(request_raw,",
    )
    assert restored.replace("request_raw", expression) == cost.provider_original()


def test_controller_reuse_reconstructs_predecessor_and_is_per_receipt():
    sql = cost.controller_sql()
    expression = "@SCHEMA@.mineagent_rd_canonical_json(r.payload,true)"
    assert sql.count(expression) == 1
    assert (
        sql.index("FOR r IN SELECT")
        < sql.index("receipt_raw:=")
        < sql.index("CASE r.kind")
    )
    restored = sql.replace(
        "started text; quiescent text; seed text; receipt_raw text;",
        "started text; quiescent text; seed text;",
    ).replace(
        "  THEN RAISE EXCEPTION 'OE controller typed receipt'; END IF;\n"
        f"  receipt_raw:={expression};\n"
        "  IF octet_length(receipt_raw) NOT BETWEEN 1 AND 16384",
        "  OR octet_length(receipt_raw) NOT BETWEEN 1 AND 16384",
    )
    assert restored.replace("receipt_raw", expression) == cost.controller_original()


def test_bundle_contains_only_two_latest_functions_and_exact_downgrade():
    assert cost.render_upgrade_sql().count("CREATE OR REPLACE FUNCTION") == 2
    assert (
        cost.render_downgrade_sql()
        == cost.provider_original() + "\n" + cost.controller_original()
    )
