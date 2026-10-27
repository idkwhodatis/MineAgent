"""Exact host recipe pins, not a claim of arbitrary image-content authenticity."""

from pathlib import Path

import pytest

from runtimes.openevolve_operational.recipe_pins import (
    PINS,
    require_declared_recipe_pins,
    verify_recipe_pins,
)
from tests.openevolve.test_contracts import complete_spec


def test_declared_current_recipe_and_actual_source_bytes_match():
    spec = complete_spec()
    require_declared_recipe_pins(spec)
    verify_recipe_pins(spec)


@pytest.mark.parametrize("field", list(PINS))
def test_unknown_recipe_commitment_rejected(field):
    with pytest.raises(ValueError, match="Unregistered"):
        verify_recipe_pins(complete_spec().model_copy(update={field: "0" * 64}))


@pytest.mark.parametrize("field", list(PINS))
def test_actual_host_recipe_drift_rejected(tmp_path, field):
    for name, (path, _) in PINS.items():
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(
            Path(path).read_bytes() + (b"#drift" if name == field else b"")
        )
    with pytest.raises(ValueError, match="source drift"):
        verify_recipe_pins(complete_spec(), root=tmp_path)
