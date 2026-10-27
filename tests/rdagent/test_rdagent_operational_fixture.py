"""Exact one-output-day fixture configuration; not execution evidence."""

import importlib.util
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from packages.factors.contracts import DateWindow


def fixture_module():
    name = "rd_source_fixture_window_probe"
    if name not in sys.modules:
        path = Path(__file__).parents[1] / "integration" / "rd_source_fixture.py"
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def test_single_output_day_preserves_prior_history_and_default_window():
    module = fixture_module()
    dataset = SimpleNamespace(id=UUID(int=2), content_sha256="a" * 64)
    original = module._config(dataset)
    window = DateWindow(
        start=original.input.window.end - timedelta(days=1),
        end=original.input.window.end,
    )
    restricted = module._config(dataset, window=window)
    assert restricted.input.window == window
    assert restricted.input.lookback_sessions == 2
    assert restricted.input.calendar_sha256 == original.input.calendar_sha256
    assert restricted.input.dataset == original.input.dataset
    assert module._config(dataset) == original
    assert restricted.sha256 != original.sha256


def test_window_override_never_accepts_unvalidated_dict():
    with pytest.raises(ValueError, match="DateWindow"):
        fixture_module()._config(
            SimpleNamespace(id=UUID(int=2), content_sha256="a" * 64), window={}
        )
