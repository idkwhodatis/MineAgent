"""Fixed wrapper structural tests; no claim of60s native kernel acceptance."""

from pathlib import Path

import pytest

from runtimes.openevolve_evaluator.protected_deadline import (
    CHILD_ARGS,
    CHILD_ENV,
    remaining_seconds,
)


def test_distinct_absolute_deadline_and_fixed_original_engine_path():
    assert remaining_seconds("61000", 1000) == 60
    assert remaining_seconds("1001", 1000) == 0.001
    assert CHILD_ARGS == ("/usr/local/bin/python", "-s", "-P", "-m", "entrypoint")
    assert CHILD_ENV["PYTHONHASHSEED"] == "0"
    assert CHILD_ENV["PYTHONPATH"] == "/opt/tradegrad"
    assert CHILD_ENV["PYTHONNOUSERSITE"] == "1"


@pytest.mark.parametrize("deadline", ["1000", "61001", "-1", "nan", "١١٠٠"])
def test_deadline_fail_closed(deadline):
    with pytest.raises(ValueError):
        remaining_seconds(deadline, 1000)


def test_derivative_copies_no_changed_native_engine():
    text = Path("runtimes/openevolve_evaluator/Dockerfile").read_text()
    allowed = (
        Path("runtimes/openevolve_evaluator/Dockerfile.dockerignore")
        .read_text()
        .splitlines()
    )
    for line in text.splitlines():
        if line.startswith("COPY "):
            for source in line.split()[1:-1]:
                assert "!" + source in allowed and Path(source).is_file()
                assert source.startswith(
                    ("runtimes/openevolve_evaluator/", "runtimes/hostile_deadline/")
                )
    assert "pip install" not in text
