"""Invocation-local reuse of exact OE commitments; every graph check stays live."""

import ast
import hashlib
from pathlib import Path

from backend.app.rdagent_provider_revision import changed, function

PINS = {
    "028_oe_structured_search.py": "dbb61d771730685de3cf8cb2fd7eeafe48e2c7c77fa54d57c19d70df2e68f7c9",
    "031_workflow_structured_stage.py": "67c916d766057c38db8f9e275de1936dcc3edf7a9803931dff597ed6b912ec84",
}


def original(filename, name):
    tree = ast.parse(
        (Path(__file__).parents[1] / "alembic/versions" / filename).read_text()
    )
    sql = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "UPGRADE_SQL" for t in node.targets)
    )
    if hashlib.sha256(sql.encode()).hexdigest() != PINS[filename]:
        raise ValueError("Exact reviewed OE predecessor required")
    return function(sql, name)


def provider_original():
    return original("031_workflow_structured_stage.py", "mineagent_oes_provider_check")


def controller_original():
    return original("028_oe_structured_search.py", "mineagent_oes_controller_check")


def provider_sql():
    sql = changed(
        provider_original(),
        "kinds text[]; raw text; config_raw text;",
        "kinds text[]; raw text; config_raw text; request_raw text;",
    )
    expression = "@SCHEMA@.mineagent_rd_canonical_json(request,true)"
    if sql.count(expression) != 2:
        raise ValueError("Exact repeated OE request commitment required")
    sql = sql.replace(expression, "request_raw")
    # Preserve all preceding rejection guards and the original exception before
    # evaluating the serializer. Only the unchanged local request is reused.
    return changed(
        sql,
        " OR o.request_sha256 IS DISTINCT FROM encode(sha256(convert_to(request_raw,",
        " THEN RAISE EXCEPTION 'OE provider exact request'; END IF;\n"
        f" request_raw:={expression};\n"
        " IF o.request_sha256 IS DISTINCT FROM encode(sha256(convert_to(request_raw,",
    )


def controller_sql():
    sql = changed(
        controller_original(),
        "started text; quiescent text; seed text;",
        "started text; quiescent text; seed text; receipt_raw text;",
    )
    expression = "@SCHEMA@.mineagent_rd_canonical_json(r.payload,true)"
    if sql.count(expression) != 2:
        raise ValueError("Exact repeated OE controller commitment required")
    sql = sql.replace(expression, "receipt_raw")
    # Assigned afresh for every loop iteration, after scope and typed checks.
    return changed(
        sql,
        "  OR octet_length(receipt_raw) NOT BETWEEN 1 AND 16384",
        "  THEN RAISE EXCEPTION 'OE controller typed receipt'; END IF;\n"
        f"  receipt_raw:={expression};\n"
        "  IF octet_length(receipt_raw) NOT BETWEEN 1 AND 16384",
    )


def render_upgrade_sql():
    return provider_sql() + "\n" + controller_sql()


def render_downgrade_sql():
    return provider_original() + "\n" + controller_original()
