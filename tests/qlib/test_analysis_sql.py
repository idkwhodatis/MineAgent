"""Generated022 fragment grammar checks, not executed PostgreSQL proof."""

import json
import re

import pytest

from backend.app.analysis_process_ddl import render_sql


def test_all_defaulted_serialized_keys_are_required():
    sql = render_sql()
    rules = re.findall(r"THEN rule := '(.*?)'::jsonb;", sql)
    assert len(rules) == 4
    for raw in rules:
        rule = json.loads(raw)
        assert set(rule["properties"]) == set(rule["required"])
        for nested in rule.get("$defs", {}).values():
            if "properties" in nested:
                assert set(nested["required"]) == set(nested["properties"])
    assert "mineagent.qlib-analysis-process-launch.v1" in sql
    assert "deadline+interval '1 second'" in sql
    assert "intent->'inputs'->>'analysis_input_sha256'" in sql


@pytest.mark.parametrize("schema", ["public;DROP", "a.b", "PUBLIC", ""])
def test_schema_interpolation_closed(schema):
    with pytest.raises(ValueError):
        render_sql(schema)
