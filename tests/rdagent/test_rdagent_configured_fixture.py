"""Configured gate's real retained bytes and preflight; no PG/Docker proof."""

import importlib.util
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app.rdagent_configured_source import derive_configured_labels
from packages.factors.contracts import DateWindow
from packages.qlib_adapter.objective import DirectObjectiveDeclaration


def module(name):
    path = Path(__file__).resolve().parents[1] / "integration" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def configured(tmp_path, monkeypatch):
    data = module("rd_configured_dataset_fixture")
    rows = []

    class Factory:
        @contextmanager
        def begin(self):
            yield SimpleNamespace(add=rows.append)

    dataset, source, policy = data.persist_configured_dataset(
        Factory(), uuid4(), SimpleNamespace(storage_root=tmp_path)
    )
    return data, dataset, source, policy, tmp_path, rows


def test_retained_bundle_source_and_separate_forward_labels(configured):
    data, dataset, source, _policy, root, rows = configured
    assert rows == [dataset]
    assert len(source.observations) == 12 and len(source.calendar.sessions) == 4
    args = data.encoded_marketdata_fixture(data.retained_marketdata_fixture())
    args["input_names"] = ("close",)
    close_source = data.parse_retained_source(**args)
    objective = DirectObjectiveDeclaration(
        window=DateWindow(start="2024-03-13", end="2024-03-14"),
        label_horizon_sessions=1,
        metric="rank_ic",
        direction="at_least",
        threshold=0.9,
        minimum_defined_days=1,
        minimum_pairs=3,
    )
    labels = derive_configured_labels(close_source, objective)
    assert len(labels.rows) == 3
    assert sorted(row.value for row in labels.rows) == pytest.approx(
        sorted([110 / 104 - 1, 104 / 102 - 1, 96 / 98 - 1])
    )
    assert all(row.horizon_end.isoformat() == "2024-03-14" for row in labels.rows)
    assert (root / dataset.storage_key / "manifest.json").read_bytes()


def test_label_helper_rejects_non_close_only_source(configured):
    _, _, source, _, _, _ = configured
    objective = DirectObjectiveDeclaration(
        window=DateWindow(start="2024-03-13", end="2024-03-14"),
        label_horizon_sessions=1,
        metric="ic",
        direction="at_least",
        threshold=0,
        minimum_defined_days=1,
        minimum_pairs=3,
    )
    with pytest.raises(ValueError, match="close-only"):
        derive_configured_labels(source, objective)
