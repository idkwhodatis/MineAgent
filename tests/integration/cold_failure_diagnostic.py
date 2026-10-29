"""Test-only bounded cold-failure locations; never exception text or retained data."""

import functools
import importlib
import json
from types import CodeType, FunctionType, TracebackType

from backend.app.operations_backup import BackupError

# Fixed import targets, not traceback-provided paths or module names.
_MODULES = (
    "backend.app.operations_backup",
    "backend.app.research_backup",
    "backend.app.factor_backup",
    "backend.app.rdagent_backup",
    "backend.app.rdagent_controller_cold_links",
    "backend.app.rdagent_controller_backup",
    "backend.app.rdagent_configured_backup",
    "backend.app.rdagent_provider_backup",
    "backend.app.rdagent_seed_backup",
    "backend.app.openevolve_process_backup",
    "backend.app.openevolve_backup",
    "backend.app.structured_factor_backup",
    "backend.app.factor_analysis_operations",
    "backend.app.factor_analysis_backup",
    "backend.app.factor_analysis_reconstruction",
    "backend.app.rdagent_configured_exposure_backup",
)


def _registry():
    result = {}

    def add(code, module):
        result[id(code)] = (
            code,
            module,
            code.co_qualname,
            frozenset(line for _, _, line in code.co_lines() if line is not None),
        )
        for item in code.co_consts:
            if type(item) is CodeType:
                add(item, module)

    for name in _MODULES:
        module = importlib.import_module(name)
        for value in vars(module).values():
            if type(value) is FunctionType and value.__module__ == name:
                add(value.__code__, name)
    return result


_CODES = _registry()
_TYPES = (
    (ValueError, "ValueError"),
    (TypeError, "TypeError"),
    (KeyError, "KeyError"),
    (AttributeError, "AttributeError"),
    (BackupError, "BackupError"),
)
_TRACEBACK_SLOT = BaseException.__dict__["__traceback__"]
_CONTEXT_SLOT = BaseException.__dict__["__context__"]


def failure_projection(error):
    """Identity-matched code objects only, including contexts hidden by from None."""
    result, seen = [], set()
    for _ in range(8):
        if error is None or id(error) in seen:
            break
        try:
            trace = _TRACEBACK_SLOT.__get__(error, BaseException)
        except TypeError:
            break
        seen.add(id(error))
        frames = []
        for _ in range(64):
            if type(trace) is not TracebackType:
                break
            entry = _CODES.get(id(trace.tb_frame.f_code))
            if (
                entry is not None
                and entry[0] is trace.tb_frame.f_code
                and type(trace.tb_lineno) is int
                and trace.tb_lineno in entry[3]
            ):
                frames.append(
                    {"module": entry[1], "function": entry[2], "line": trace.tb_lineno}
                )
            trace = trace.tb_next
        family = next((label for kind, label in _TYPES if type(error) is kind), "other")
        result.append({"type": family, "frames": frames})
        error = _CONTEXT_SLOT.__get__(error, BaseException)
    return {"schema": "mineagent.cold-failure-location.v1", "chain": result}


def diagnose_cold_failure(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except BackupError as error:
            try:
                print(
                    "COLD_FAILURE_LOCATION:"
                    + json.dumps(failure_projection(error), sort_keys=True),
                    flush=True,
                )
            except Exception:  # noqa: BLE001,S110 - diagnostics cannot replace failure
                pass
            raise

    return wrapped
