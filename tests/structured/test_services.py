"""Real FD and declared-input checks; SQL authority doubles are not PG proof."""

import hashlib
import json
import os
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "integration"))
from structured_market_fixture import encoded_structured_market

from backend.app.factor_native_io import NativeStorageError, native_storage_guard
from backend.app.services import DomainError
from backend.app.structured_factor_io import read_structured_artifact
from packages.factors.contracts import FeatureRow, SnapshotInput, digest, rows_digest
from packages.factors.execution_snapshot import ExecutionSnapshotManifest
from packages.rdagent_adapter.configured_source import ARTIFACTS, parse_retained_source
from packages.research_runtime.budget_contracts import BudgetVector
from packages.structured_factor_strategy.assembly import assemble_input
from packages.structured_factor_strategy.market import stage_market
from packages.structured_factor_strategy.run import StructuredRunConfig
from packages.structured_factor_strategy.wire import read_input, write_input
from tests.structured.test_operator import declared_input


def material():
    args = encoded_structured_market()
    source = parse_retained_source(
        **dict(args, artifacts={k: args["artifacts"][k] for k in ARTIFACTS})
    )
    old = declared_input()
    value = old.snapshot.input.model_dump(mode="json")
    value.update(
        dataset=source.dataset.model_dump(mode="json"),
        calculation_universe=source.universe.model_dump(mode="json"),
        calendar_sha256=source.calendar.sha256,
        market_timezone=source.calendar.timezone,
    )
    value["definition"]["input_fields"] = [
        x.model_dump(mode="json") for x in source.input_fields
    ]
    # Existing definition's field list happens to be close/volume; rebind exact commitment explicitly.
    from packages.factors.contracts import FactorDefinition

    definition = FactorDefinition.model_validate(value["definition"])
    value["version"]["definition_sha256"] = definition.sha256
    inp = SnapshotInput.model_validate(value)
    rows = tuple(
        FeatureRow.model_validate({**r.model_dump(mode="json"), "listing_id": key})
        for r, key in zip(old.rows, source.universe.listing_ids, strict=True)
    )
    manifest = old.snapshot.model_dump(mode="json")
    manifest.update(
        input=inp.model_dump(mode="json"),
        input_sha256=inp.sha256,
        output_rows_sha256=rows_digest(rows),
    )
    snapshot = ExecutionSnapshotManifest.model_validate(manifest)
    config = StructuredRunConfig(
        structured_strategy_version_id=old.structured_strategy_version_id,
        program_sha256=digest(old.program),
        dataset_version_id=args["dataset_id"],
        dataset_sha256=args["manifest_sha256"],
        snapshots=(snapshot.snapshot_id,),
        execution_window={"start": date(2024, 3, 13), "end": date(2024, 3, 15)},
        initial_cash=10000.0,
        source_policy=args["policy"],
        engine_image_id="sha256:" + "a" * 64,
        engine_installed_parent_image_id="sha256:" + "b" * 64,
        engine_dependency_lock_sha256="c" * 64,
        budget=BudgetVector(
            process_slots=1,
            evaluator_executions=1,
            wall_milliseconds=90000,
            storage_bytes=268500992,
        ),
    )
    market = stage_market(
        args["manifest_bytes"],
        args["artifacts"],
        dataset_sha256=args["manifest_sha256"],
        window=config.execution_window,
    )
    fence = NS(
        **{
            k: getattr(old, k)
            for k in ("owner_id", "run_id", "attempt_id", "generation")
        }
    )
    return {
        "fence": fence,
        "execution_id": old.execution_id,
        "config": config,
        "program": old.program,
        "snapshot": snapshot,
        "rows": rows,
        "source": source,
        "market": market,
    }


def test_exact_declared_assembly_has_no_labels_and_no_symbol_inference():
    kw = material()
    value = assemble_input(**kw)
    assert read_input(write_input(value)) == value
    assert value.market_sha256 == hashlib.sha256(kw["market"].content).hexdigest()
    assert all(
        x.factor_listing_id == str(x.native_listing_id) for x in value.listing_bindings
    )
    assert "labels" not in json.loads(write_input(value))


def test_same_program_and_market_binds_distinct_exact_snapshot():
    kw = material()
    first = assemble_input(**kw)
    bad = kw["config"].model_copy(update={"dataset_sha256": "d" * 64})
    with pytest.raises(ValueError, match="declaration"):
        assemble_input(**dict(kw, config=bad))
    assert first.engine.manifest.authoring is None


def test_run_budget_accounts_both_full_units():
    config = material()["config"]
    with pytest.raises(ValueError, match="budget"):
        StructuredRunConfig.model_validate(
            {
                **config.model_dump(mode="json"),
                "snapshots": [str(config.snapshots[0]), str(uuid4())],
            }
        )


@pytest.fixture
def retained(tmp_path):
    (tmp_path / "factors").mkdir()
    key = f"datasets/{uuid4()}/{uuid4()}"
    path = tmp_path / key
    path.mkdir(parents=True)
    raw = b"[]"
    (path / "actions.json").write_bytes(raw)
    return tmp_path, key, path, hashlib.sha256(raw).hexdigest()


def test_fixed_leaf_requires_retained_guard(retained):
    root, key, _path, sha = retained
    with pytest.raises(NativeStorageError):
        read_structured_artifact(root, key, "actions.json", sha, 32)
    with native_storage_guard(root):
        assert read_structured_artifact(root, key, "actions.json", sha, 32) == b"[]"
        with pytest.raises(DomainError):
            read_structured_artifact(root, key, "../actions.json", sha, 32)
        with pytest.raises(DomainError):
            read_structured_artifact(root, key, "actions.json", "f" * 64, 32)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "oversize"])
def test_unsafe_leaf_rejects(retained, kind):
    root, key, path, sha = retained
    if kind == "symlink":
        (path / "actions.json").unlink()
        (path / "actions.json").symlink_to("/dev/null")
    elif kind == "hardlink":
        os.link(path / "actions.json", path / "other")
    with native_storage_guard(root), pytest.raises((DomainError, OSError)):
        read_structured_artifact(
            root, key, "actions.json", sha, 1 if kind == "oversize" else 32
        )


def test_current_process_reader_cannot_bypass_transaction():
    from backend.app.structured_factor_services import require_structured_execution

    with pytest.raises(DomainError):
        require_structured_execution(NS(info={}), NS(), uuid4())
