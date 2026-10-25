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


def test_real_entrypoint_respects_lower_inherited_hard_limit(tmp_path):
    """Execute actual parser entrypoint with Docker-equivalent 16MiB hard fsize."""
    import json
    import subprocess
    import sys

    source = write_partition([sample()], tmp_path / "input.parquet")
    output = tmp_path / "out"
    output.mkdir()
    script = """
import resource,sys
from pathlib import Path
from packages.factors import parser
source,output=map(Path,sys.argv[1:]);sys.argv=['factor-parser']
resource.setrlimit(resource.RLIMIT_FSIZE,(16*1024*1024,16*1024*1024))
code=parser.main(input_path=source,output_directory=output)
assert resource.getrlimit(resource.RLIMIT_FSIZE)==(16*1024*1024,16*1024*1024)
raise SystemExit(code)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(source.path), str(output)],
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    line, partition = result.stdout.split(b"\n", 1)
    report = json.loads(line)
    assert report["status"] == "validated" and report["input_sha256"] == source.sha256
    assert len(partition) == report["output_size_bytes"]
    import hashlib

    assert hashlib.sha256(partition).hexdigest() == report["output_sha256"]


def test_tightening_preserves_all_smaller_soft_and_hard_caps(monkeypatch):
    from packages.factors import parser

    inherited = {
        parser.resource.RLIMIT_CORE: (0, 0),
        parser.resource.RLIMIT_CPU: (4, 6),
        parser.resource.RLIMIT_FSIZE: (4096, 8192),
        parser.resource.RLIMIT_NOFILE: (32, 48),
    }
    calls = {}
    monkeypatch.setattr(parser.resource, "getrlimit", lambda kind: inherited[kind])
    monkeypatch.setattr(
        parser.resource,
        "setrlimit",
        lambda kind, values: calls.__setitem__(kind, values),
    )
    parser.tighten_runtime_limits()
    assert calls == {kind: (values[0], values[0]) for kind, values in inherited.items()}
