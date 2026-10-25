"""Pure contracts and real filesystem safety; not PostgreSQL acceptance."""

import hashlib
import os
import time
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.app.factor_schemas import (
    FactorBundleCreate,
    FactorCreate,
    FixedFixtureCreate,
)
from backend.app.factor_services import check_deadline, physical_inventory
from backend.app.services import DomainError
from packages.factors.fixtures import fixed_factor_code, fixture_definition


def test_exact_source_keeps_line_endings_and_invalid_syntax():
    source = "def broken(:\r\n  pass\r\n"
    definition = fixture_definition("momentum").model_copy(
        update={"code_sha256": hashlib.sha256(source.encode()).hexdigest()}
    )
    item = FactorCreate(name="Not executed", definition=definition, code=source)
    assert item.code == source
    with pytest.raises(ValidationError):
        FactorCreate(
            name="changed", definition=definition, code=source.replace("\r\n", "\n")
        )


@pytest.mark.parametrize("source", ["\x00", " ", "😀" * 40000])
def test_source_rejects_nul_blank_or_utf8_overflow(source):
    definition = fixture_definition("momentum").model_copy(
        update={"code_sha256": hashlib.sha256(source.encode()).hexdigest()}
    )
    with pytest.raises(ValidationError):
        FactorCreate(name="bad", definition=definition, code=source)


def test_new_factor_rejects_cross_factor_parent_and_extra_verification():
    payload = {
        "name": "x",
        "definition": fixture_definition("momentum"),
        "code": fixed_factor_code("momentum"),
    }
    with pytest.raises(ValidationError):
        FactorCreate(**payload, parent_version_id=uuid4())
    with pytest.raises(ValidationError):
        FactorCreate(**payload, local_verification={"kind": "verified_source"})


def test_bundle_rejects_duplicate_version():
    member = {"factor_version_id": uuid4(), "definition_sha256": "a" * 64}
    with pytest.raises(ValidationError):
        FactorBundleCreate(name="x", members=[member, member])


@pytest.mark.parametrize("value", [True, 0, 6])
def test_fixture_parameters_are_bounded_strict(value):
    with pytest.raises(ValidationError):
        FixedFixtureCreate(lookback_sessions=value)


def inventory(root, owner, maximum=100):
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        return physical_inventory(fd, owner, time.monotonic() + 5, maximum)
    finally:
        os.close(fd)


def test_inventory_counts_registered_orphan_and_dataset_bytes(tmp_path):
    owner = uuid4()
    other = uuid4()
    for user in (owner, other):
        directory = tmp_path / str(user) / "objects"
        directory.mkdir(parents=True)
        (directory / ("a" * 64 + ".json")).write_bytes(b"123")
    dataset = tmp_path / str(owner) / "datasets" / str(uuid4())
    dataset.mkdir(parents=True)
    (dataset / "manifest.json").write_bytes(b"{}")
    assert inventory(tmp_path, owner)[:2] == (8, 5)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "unknown", "staging"])
def test_inventory_fails_closed_on_unsafe_or_unresolved_entries(tmp_path, kind):
    owner = uuid4()
    directory = tmp_path / str(owner) / "objects"
    directory.mkdir(parents=True)
    target = directory / ("a" * 64 + ".json")
    if kind == "symlink":
        target.symlink_to("/etc/passwd")
    elif kind == "hardlink":
        source = tmp_path / "source"
        source.write_bytes(b"x")
        os.link(source, target)
    elif kind == "fifo":
        os.mkfifo(target)
    elif kind == "unknown":
        (directory / "unexpected").write_bytes(b"x")
    else:
        (tmp_path / str(owner) / "staging" / str(uuid4())).mkdir(parents=True)
    with pytest.raises(DomainError):
        inventory(tmp_path, owner)


def test_inventory_file_count_and_deadline_are_bounded(tmp_path):
    owner = uuid4()
    (tmp_path / str(owner)).mkdir()
    with pytest.raises(DomainError):
        inventory(tmp_path, owner, maximum=0)
    with pytest.raises(DomainError):
        check_deadline(time.monotonic() - 1)
