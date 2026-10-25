"""Parser semantics on trusted fixtures; NOT proof of external sandbox containment."""

import pytest

from packages.factors.parser import parse_partition, validate_report
from packages.factors.storage import FactorStorageError, read_page, write_partition

from .test_storage import sample


def test_canonical_derived_provenance(tmp_path):
    source = write_partition([sample()], tmp_path / "input.parquet")
    original = source.path.read_bytes()
    out = tmp_path / "out"
    out.mkdir()
    report = parse_partition(source.path, out)
    assert report["input_sha256"] == source.sha256
    assert report["temporal_verification"] == "declared_or_unknown"
    assert report["identity_mode"] == "derived"
    assert source.path.read_bytes() == original
    assert len(read_page(out / "values.parquet", report["output_sha256"])) == 1
    validate_report(report)
    for key, value in [
        ("output_name", "../control/terminal.json"),
        ("valid_count", True),
        ("temporal_verification", "verified_source"),
        ("output_sha256", "x"),
    ]:
        bad = dict(report)
        bad[key] = value
        with pytest.raises(FactorStorageError):
            validate_report(bad)


@pytest.mark.parametrize(
    "data",
    [b"pickle", b"PAR1" + b"x" * 20 + (2**32 - 1).to_bytes(4, "little") + b"PAR1"],
)
def test_bad_header_or_footer(tmp_path, data):
    path = tmp_path / "input.parquet"
    path.write_bytes(data)
    with pytest.raises(FactorStorageError):
        parse_partition(path, tmp_path)


def test_reject_multiple_versions_before_output(tmp_path):
    a = sample()
    b = sample()
    b["factor_version_id"] = "00000000-0000-0000-0000-000000000002"
    source = write_partition([a, b], tmp_path / "input.parquet")
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(FactorStorageError):
        parse_partition(source.path, out)
    assert list(out.iterdir()) == []


def test_reject_nested_native_schema(tmp_path):
    import duckdb

    path = tmp_path / "input.parquet"
    with duckdb.connect() as con:
        con.execute(
            "COPY (SELECT [1,2,3] AS nested) TO ? (FORMAT PARQUET)", [str(path)]
        )
    with pytest.raises(FactorStorageError):
        parse_partition(path, tmp_path)


def test_derived_identity_mapping_preserves_original(tmp_path):
    import hashlib
    import json

    source = write_partition([sample()], tmp_path / "input.parquet")
    original = source.path.read_bytes()
    mapping = tmp_path / "identity-map.json"
    mapping.write_text(
        json.dumps(
            {
                "schema_version": "mineagent.factor-identity-map.v1",
                "source_factor_version_id": sample()["factor_version_id"],
                "local_factor_version_id": "00000000-0000-0000-0000-000000000099",
            }
        )
    )
    out = tmp_path / "output"
    out.mkdir()
    report = parse_partition(source.path, out, identity_map_path=mapping)
    assert (
        report["identity_mapping_sha256"]
        == hashlib.sha256(mapping.read_bytes()).hexdigest()
    )
    assert report["source_factor_version_ids"] == [sample()["factor_version_id"]]
    assert report["factor_version_ids"] == ["00000000-0000-0000-0000-000000000099"]
    assert report["output_sha256"] != source.sha256
    assert source.path.read_bytes() == original
