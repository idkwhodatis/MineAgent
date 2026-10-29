"""Offline renderer checks only; not PostgreSQL parsing or execution evidence."""

import hashlib
from pathlib import Path

import pytest

from backend.app import rdagent_source_sql as sql


def test_source_pinned_deterministic_disabled_fragment():
    first = sql.render_sql("rd_source_test")
    assert first == sql.render_sql("rd_source_test")
    assert "INCOMPLETE DISABLED REVIEW FRAGMENT" in first
    assert "SET search_path=pg_catalog" in first
    assert "public." not in first
    assert "ALTER TABLE" not in first and "DROP " not in first
    assert "DISABLE TRIGGER" not in first and "CREATE TABLE" not in first
    root = Path(sql.__file__).resolve().parents[2]
    for name, expected in sql.SOURCE_PINS.items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() in (
            expected,
            sql.SUCCESSOR_SOURCE_PINS.get((name, expected)),
        )


@pytest.mark.parametrize(
    "schema", ["", "x;drop", "a.b", '"public"', "UPPER", "x" * 64, None, True]
)
def test_schema_identifier_is_not_arbitrary_sql(schema):
    with pytest.raises(ValueError):
        sql.render_sql(schema)


def test_source_pin_drift_is_fail_closed(monkeypatch):
    name = "backend/app/rdagent_models.py"
    original = sql.SOURCE_PINS[name]
    # Registering one exact reviewed successor never accepts a changed pin or
    # another purported successor hash, including the known historic source.
    with monkeypatch.context() as patch:
        patch.setattr(sql, "SOURCE_PINS", {name: "0" * 64})
        with pytest.raises(ValueError, match="source-pin drift"):
            sql.render_sql()
    with monkeypatch.context() as patch:
        patch.setitem(sql.SUCCESSOR_SOURCE_PINS, (name, original), "0" * 64)
        with pytest.raises(ValueError, match="source-pin drift"):
            sql.render_sql()
    assert sql.render_sql()


def test_all_mutation_participants_have_deferred_triggers():
    text = sql.render_sql()
    assert len(sql.TRIGGER_TABLES) == len(set(sql.TRIGGER_TABLES))
    for table in sql.TRIGGER_TABLES:
        assert (
            f'AFTER INSERT OR UPDATE OR DELETE ON "public"."{table}" DEFERRABLE INITIALLY DEFERRED'
            in text
        )
    assert text.count("CREATE CONSTRAINT TRIGGER") == len(sql.TRIGGER_TABLES)
    assert {
        "research_model_operations",
        "research_model_transports",
        "research_model_outcomes",
        "research_budget_reservations",
        "research_budget_settlements",
        "factor_source_read_witnesses",
        "factor_source_read_parts",
        "factor_objects",
        "factor_versions",
        "engine_object_reservation_consumers",
    } <= set(sql.TRIGGER_TABLES)


@pytest.mark.parametrize(
    "reason",
    [
        "source_missing",
        "run_identity",
        "start_scope",
        "generation_call",
        "assembly_scope",
        "source_read_witness",
        "parser_identity",
        "call_completion",
        "controller_scope",
        "controller_admission",
        "factor_version",
        "source_code_object",
        "source_publication",
        "source_publication_outcome",
        "source_reservation",
        "source_consumer_claim",
        "member_cardinality",
        "member_order_scope",
        "operation_scope",
        "physical_transport_closure",
        "completed_response",
        "selected_assembly_part",
        "selected_read_part",
        "completed_call_inventory",
        "selected_inventory_size",
        "witness_metadata_digest",
    ],
)
def test_declared_relational_failure_branches_are_rendered(reason):
    assert f"RETURN '{reason}'" in sql.render_sql()


def test_selected_chain_and_witness_fields_not_payload_claims():
    text = sql.render_sql()
    assert "max(request_ordinal) FILTER (WHERE relation<>'continuation')" in text
    assert "idx-selected_first" in text
    assert "IS DISTINCT FROM to_jsonb(member_ids)" in text
    assert "sum(content_size_bytes)" in text
    assert "part_documents:=part_documents||jsonb_build_array" in text
    assert "mineagent_rd_ascii_metadata(metadata)" in text
    assert "w.raw_text" not in text.replace("w.raw_text_bytes", "")
    assert "w.normalized_json" not in text
    assert "pg_read_file" not in text and "COPY " not in text


def test_seeded_profile_model_pin_does_not_rewrite_historical_source_sql():
    assert hashlib.sha256(sql.render_sql().encode()).hexdigest() == (
        "3b7242168ed968c778057f1eebebc78e09da49e2e7ffffb991f07498736d1938"
    )
