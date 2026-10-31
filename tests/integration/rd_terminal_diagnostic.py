"""Test-only fixed terminal-gate categories, never frame paths or retained data."""

import ast
import hashlib
import json
import sys
from collections import deque
from contextlib import contextmanager
from pathlib import Path

SOURCE_SHA256 = "1fae361d616f747199fae982d148d3985bae3d3de30e3c93cc1a8b8b81c17787"
CATEGORIES = {
    "_settlements": (
        "settlement_inventory",
        "settlement_membership",
        "settlement_charge",
    ),
    "read_rd_terminal_in_session": (
        "attempt_containment",
        "provider_containment_before",
        "process_admission_membership",
        "controller_stop",
        "controller_receipt_membership",
        "provider_containment_after",
    ),
}


def source_sites(service):
    """Unknown source revisions are unclassified, never interpreted by position."""
    try:
        source = Path(service.__file__).read_bytes()
        if hashlib.sha256(source).hexdigest() != SOURCE_SHA256:
            return {}
        sites = {}
        for function in ast.parse(source).body:
            if (
                not isinstance(function, ast.FunctionDef)
                or function.name not in CATEGORIES
            ):
                continue
            calls = sorted(
                node.lineno
                for node in ast.walk(function)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "unavailable"
                and len(node.args) == 1
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "workflow_waiting_containment"
            )
            categories = CATEGORIES[function.name]
            if len(calls) != len(categories):
                return {}
            code = getattr(service, function.name).__code__
            sites.update(
                {(code, line): category for line, category in zip(calls, categories)}
            )
        return sites if len(sites) == 9 else {}
    except Exception:  # noqa: BLE001 -- diagnostics cannot replace the real gate
        return {}


def classify(sites, frame):
    return sites.get((frame.f_code, frame.f_lineno), "other")


@contextmanager
def diagnose_rd_terminal():
    from backend.app import research_workflow_rd_terminal as service

    original = service.unavailable
    sites = source_sites(service)
    categories = deque(maxlen=8)
    count = 0

    def unavailable(*args, **kwargs):
        nonlocal count
        try:
            code = args[0] if len(args) == 1 and not kwargs else kwargs.get("code")
            if type(code) is str and code == "workflow_waiting_containment":
                categories.append(classify(sites, sys._getframe(1)))
                count = min(count + 1, 10000)
        except Exception:  # noqa: BLE001, S110 -- no diagnostic exception disclosure
            pass
        return original(*args, **kwargs)

    service.unavailable = unavailable
    try:
        yield
    finally:
        service.unavailable = original
        try:
            print(
                "RD_TERMINAL_FAILURES:"
                + json.dumps(
                    {"count": count, "categories": list(categories)}, sort_keys=True
                )
            )
        except Exception:  # noqa: BLE001, S110 -- preserve original gate/assertion
            pass
