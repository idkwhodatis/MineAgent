"""Source-only scope checks, not PostgreSQL or native acceptance evidence."""

import hashlib
import re

import pytest

from backend.app.rdagent_provider_revision import function
from script import schema_keyset_candidate as candidate


def test_exact_frozen_pin_and_single_pure_comparison_replacement():
    frozen = candidate.frozen_sql()
    assert hashlib.sha256(frozen.encode()).hexdigest() == candidate.PIN
    original = function(frozen, candidate.NAME)
    proposed = candidate.schema_sql()
    assert proposed.count(candidate.NEW) == 1
    assert proposed.replace(candidate.NEW, candidate.OLD) == original
    assert candidate.restore_schema_sql() == original
    assert candidate.dependencies_sql() == function(frozen, candidate.HELPER)
    assert candidate.baseline_schema_sql() == (
        candidate.dependencies_sql() + "\n" + original
    ).replace(candidate.NAME, "old_rd_schema")


def test_no_exception_frame_recursion_security_or_validator_changes():
    sql = candidate.schema_sql()
    assert sql.count("EXCEPTION WHEN data_exception THEN RETURN false;") == 1
    assert sql.count("CREATE OR REPLACE FUNCTION") == 1
    assert "IMMUTABLE SET search_path=pg_catalog" in sql
    assert set(re.findall(r"@SCHEMA@\.([a-z_0-9]+)\(", sql)) == {
        candidate.NAME,
        candidate.HELPER,
        "mineagent_structured_json_value",
    }
    assert candidate.NEW.count("ARRAY(SELECT jsonb_object_keys(") == 2
    assert "ORDER BY" not in candidate.NEW
    assert "array_agg" not in candidate.NEW
    assert "jsonb_path" not in candidate.NEW
    assert not re.search(
        r"\b(?:GRANT|REVOKE|ALTER|INSERT|UPDATE|DELETE|EXECUTE|SECURITY)\b", sql
    )


def test_generator_refuses_different_baseline(monkeypatch):
    monkeypatch.setattr(candidate, "PIN", "0" * 64)
    with pytest.raises(ValueError, match="Exact published 032f"):
        candidate.schema_sql()
