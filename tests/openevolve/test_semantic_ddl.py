"""Static fragment provenance checks, not PostgreSQL execution."""

import hashlib
from pathlib import Path

from backend.app.openevolve_semantic_ddl import MODEL_SHA256, fragment


def test_semantic_fragment_is_explicit_disabled_and_snapshot_bound():
    sql = fragment()
    assert sql.count("CREATE CONSTRAINT TRIGGER oe_initial_semantics") == 16
    assert sql.count("DEFERRABLE INITIALLY DEFERRED") == 16
    assert "SECURITY DEFINER" not in sql.replace(
        "-- No SECURITY DEFINER", "-- No privileged definer"
    )
    assert "EXECUTE format" not in sql
    assert "CREATE FUNCTION mineagent_oe_initial_semantics" in sql
    root = Path(__file__).resolve().parents[2] / "backend/app"
    assert all(
        hashlib.sha256((root / k).read_bytes()).hexdigest() == v
        for k, v in MODEL_SHA256.items()
    )


def test_fragment_keeps_external_bytes_and_resume_outside_sql_claims():
    sql = fragment()
    assert "does NOT recompute native" in sql
    assert "or resume grant" in sql
    assert "'OE domain control coverage differs'" in sql
    assert "'OE worker objective agreement differs'" in sql
