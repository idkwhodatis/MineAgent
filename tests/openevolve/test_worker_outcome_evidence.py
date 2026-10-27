"""Actual result projection grammar; no execution or persistence claim."""

import copy
import hashlib
from uuid import uuid4

import pytest

from packages.openevolve_adapter.checkpoint_native import canonical
from packages.openevolve_adapter.object_identity import parse_document
from packages.openevolve_adapter.worker_outcome_evidence import SCHEMA, source_sha256


def document():
    parent = str(uuid4())
    child = {
        "id": str(uuid4()),
        "code": "probe = 0\n",
        "changes_description": "",
        "language": "python",
        "parent_id": parent,
        "generation": 1,
        "timestamp": 1.0,
        "iteration_found": 1,
        "metrics": {"combined_score": -5.0},
        "complexity": 0.0,
        "diversity": 0.0,
        "metadata": {"island": 0},
        "prompts": None,
        "artifacts_json": None,
        "artifact_dir": None,
        "embedding": None,
    }
    return {
        "schema_version": SCHEMA,
        "event_sha256": "a" * 64,
        "projection": {
            "epoch_id": str(uuid4()),
            "intent_id": str(uuid4()),
            "native_iteration": 1,
            "status": "evaluated",
            "objective_ack_id": str(uuid4()),
        },
        "observation": {
            "iteration": 1,
            "outcome": "evaluated",
            "child": child,
            "parent_id": parent,
            "target_island": 0,
        },
    }


def parse(value):
    return parse_document("search_worker_outcome", canonical(value))


def test_actual_child_retained_and_source_bound():
    value = document()
    assert parse(value) == value
    assert source_sha256(value) == hashlib.sha256(b"probe = 0\n").hexdigest()
    assert "grant" not in canonical(value).decode()


def test_pinned_error_defaults_without_fake_child():
    value = document()
    value["projection"].update(status="worker_error", objective_ack_id=None)
    value["observation"].update(
        outcome="worker_error", child=None, parent_id=None, target_island=None
    )
    assert parse(value) == value
    assert source_sha256(value) is None


@pytest.mark.parametrize(
    "path,replacement",
    [
        (("projection", "native_iteration"), True),
        (("observation", "iteration"), True),
        (("observation", "iteration"), 2),
        (("observation", "target_island"), False),
        (("observation", "target_island"), 1),
        (("projection", "objective_ack_id"), None),
        (("event_sha256",), "x" * 64),
        (("observation", "child", "code"), "x" * 32769),
        (("observation", "child", "parent_id"), str(uuid4())),
        (("observation", "child", "iteration_found"), True),
        (("observation", "child", "generation"), True),
        (("observation", "child", "metrics"), {"combined_score": True}),
        (("observation", "child", "embedding"), [1.0]),
        (("observation", "child", "artifact_dir"), "/work"),
        (("observation", "child", "timestamp"), float("inf")),
        (("observation", "child", "extra"), None),
        (("observation", "grant"), "secret"),
        (("observation", "outcome"), "worker_error"),
    ],
)
def test_closed_native_projection(path, replacement):
    value = copy.deepcopy(document())
    obj = value
    for key in path[:-1]:
        obj = obj[key]
    obj[path[-1]] = replacement
    with pytest.raises(ValueError):
        parse(value)
