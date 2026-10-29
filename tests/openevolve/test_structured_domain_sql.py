"""SQL construction/relationship inventory only; PostgreSQL gate still required."""

from backend.app import oe_structured_domain_sql as domain


def test_closed_typed_contracts_and_raw_canonical_commitments():
    sql = domain.render_upgrade_sql()
    for schema in (
        "mineagent.oe-structured-candidate.v1",
        "mineagent.oe-structured-measurement.v1",
        "mineagent.oe-structured-provider-run.v1",
    ):
        assert schema in sql
    assert "@RULES@" not in sql
    assert "mineagent_rd_schema(v,rule" in sql
    assert "mineagent_structured_raw_commitment" in sql
    assert "mineagent_rd_canonical_json" not in sql


def test_candidate_parent_and_fixed_domain_exact_not_scoped_fk_only():
    sql = domain.CANDIDATE
    assert (
        "v.program-ARRAY['threshold','top_k','maximum_weight','maximum_gross_weight']"
        in sql
    )
    assert "v.parent_version_id IS DISTINCT FROM p.program_version_id" in sql
    assert "p.ordinal>=c.ordinal" in sql
    assert "x.epoch_id,x.parent_candidate_id,i.ordinal,i.parent_candidate_id" in sql
    assert "oe_structured_engine_outcomes o WHERE o.candidate_id=p.id" in sql


def test_execution_snapshot_program_and_original_row_bytes_all_bound():
    sql = domain.EXECUTION
    for text in (
        "c.program_version_id,s.snapshot_id",
        "done.manifest_sha256",
        "done.output_sha256",
        "raw->'rows'",
        "e.strategy_input_size_bytes",
    ):
        assert text in sql


def test_engine_outcome_binds_exact_receipt_hashes_and_full_charge():
    sql = domain.OUTCOME
    for text in (
        "mineagent_structured_stopped(a.id,true) IS NOT TRUE",
        "st.payload_sha256",
        "ex.payload_sha256",
        "bs.charge=b.amounts AND bs.outcome='conservative_unknown'",
        "e.candidate_id",
        "o.objective_sha256 IS DISTINCT FROM oh",
        "o.result_size_bytes",
    ):
        assert text in sql
    # Admission input hash commits the typed input binding, not strategy bytes.
    assert "a.input_sha256,e.strategy_input_sha256" not in sql
    assert "li.payload->'inputs' IS DISTINCT FROM jsonb_build_object" in sql


def test_helpers_not_standalone_activation_or_false_file_attestation():
    sql = domain.render_upgrade_sql()
    assert "CREATE TRIGGER" not in sql
    assert "CREATE TABLE" not in sql
    for name in ("outcome", "execution", "candidate"):
        assert (
            f"DROP FUNCTION @SCHEMA@.mineagent_oes_{name}_check(uuid)"
            in domain.render_downgrade_sql()
        )
