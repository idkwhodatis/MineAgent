"""Bounded input ABI only, no native evaluator execution or launch authority."""

import hashlib
from datetime import date
from uuid import uuid4

import pytest

from packages.research_runtime.process_contracts import canonical
from runtimes.openevolve_evaluator.contracts import (
    NativeInputFile,
    NativeInputManifest,
    check_input_bytes,
    parse_manifest,
    path_kind,
)


def manifest():
    files = {
        "strategy.py": b"pass\n",
        "request.json": b"{}",
        "stage-report.json": b"{}",
        "data/INDEX.csv": b"date,close\n2020-01-01,1\n",
    }
    value = NativeInputManifest(
        evaluation_id=uuid4(),
        execution_plan_sha256="a" * 64,
        source_sha256=hashlib.sha256(files["strategy.py"]).hexdigest(),
        window_start=date(2020, 1, 1),
        window_end=date(2020, 1, 2),
        files=tuple(
            NativeInputFile(
                path=p, sha256=hashlib.sha256(v).hexdigest(), size_bytes=len(v)
            )
            for p, v in sorted(files.items())
        ),
    )
    return value, files


def test_exact_manifest_roundtrip_and_bytes():
    value, files = manifest()
    assert parse_manifest(canonical(value.model_dump(mode="json"))) == value
    check_input_bytes(value, files)
    assert all(f.destination.startswith("/input/") for f in value.files)


@pytest.mark.parametrize(
    "path",
    [
        "../strategy.py",
        "/data/A.csv",
        "data/.hidden.csv",
        "data/a/b.csv",
        "universe/members_list_2020-02-30.csv",
        "data/A.csv\x00",
        "request.py",
    ],
)
def test_paths_closed(path):
    with pytest.raises(ValueError):
        path_kind(path)


def test_no_missing_or_changed_file_can_preserve_inventory():
    value, files = manifest()
    for changed in (
        {**files, "evil.py": b"x"},
        {k: v for k, v in files.items() if k != "request.json"},
        {**files, "strategy.py": b"evil\n"},
    ):
        with pytest.raises(ValueError):
            check_input_bytes(value, changed)


def test_native_inventory_hash_is_distinct_from_scoped_envelope():
    value, _ = manifest()
    expected = hashlib.sha256(
        canonical(
            [
                {"path": f.path, "sha256": f.sha256, "bytes": f.size_bytes}
                for f in value.files
            ]
        )
    ).hexdigest()
    assert value.native_artifacts_sha256 == expected
    assert value.sha256 != expected
