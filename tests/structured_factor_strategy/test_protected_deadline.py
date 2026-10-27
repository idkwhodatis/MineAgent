"""Fixed trusted-engine wrapper assertions, no native lifecycle acceptance."""

from pathlib import Path

import pytest

from runtimes.structured_factor_engine.protected_deadline import (
    ENGINE_ARGS,
    ENGINE_ENV,
    remaining_seconds,
)


def test_exact_isolated_child_and_no_caller_environment():
    assert ENGINE_ARGS == ("-I", "-m", "runtimes.structured_factor_engine.entrypoint")
    env = dict(ENGINE_ENV)
    assert "PYTHONPATH" not in env and "PYTHONHOME" not in env
    assert env["TRADEGRAD_PORTABLE_ENGINE_CONTAINER"] == "1"
    assert remaining_seconds("61000", 1000) == 60
    text = Path("runtimes/structured_factor_engine/protected_deadline.py").read_text()
    assert "os.setresuid" in text and "os.umask(0o022)" in text
    assert "os.dup2(null, descriptor)" in text


@pytest.mark.parametrize("value", ["1000", "61001", "-1", "1.0", "NaN", " 1001"])
def test_no_extended_or_noncanonical_deadline(value):
    with pytest.raises(ValueError):
        remaining_seconds(value, 1000)
