"""Only pinned observed sampling deltas may advance the control cursor."""

import copy

import pytest
from test_checkpoint_native import native as native_fixture


@pytest.fixture
def native(tmp_path):
    return native_fixture.__wrapped__(tmp_path)


from packages.openevolve_adapter.checkpoint_native import validate_directory
from packages.openevolve_adapter.sampling_contracts import validate_sampling_transition


def transition(native):
    before = validate_directory(native)
    parent = next(iter(before["programs"]))
    after = copy.deepcopy(before)
    size = float(len(before["programs"][parent]["code"]))
    after["metadata"]["feature_stats"] = {
        "complexity": {"min": size, "max": size, "values": [size, size]}
    }
    return before, after, parent, {"islands": [[parent]]}


def test_exact_observed_sampling_delta(native):
    args = transition(native)
    assert validate_sampling_transition(*args) == args[1]


@pytest.mark.parametrize(
    "fault",
    [
        "program",
        "frontier",
        "archive",
        "count",
        "value",
        "bool",
        "diversity",
        "extrema",
    ],
)
def test_sampling_cannot_exempt_unrelated_or_wrong_statistics(native, fault):
    before, after, parent, snapshot = transition(native)
    stats = after["metadata"]["feature_stats"]["complexity"]
    if fault == "program":
        after["programs"][parent]["metadata"]["invented"] = True
    elif fault == "frontier":
        after["metadata"]["last_iteration"] = 1
    elif fault == "archive":
        after["metadata"]["archive"] = []
    elif fault == "count":
        stats["values"].append(stats["max"])
    elif fault == "value":
        stats["values"][0] += 1
        stats["max"] += 1
    elif fault == "bool":
        stats["values"][0] = True
    elif fault == "diversity":
        after["metadata"]["feature_stats"]["diversity"] = {
            "min": 0.0,
            "max": 0.0,
            "values": [0.0],
        }
    else:
        stats["max"] += 1
    with pytest.raises(ValueError):
        validate_sampling_transition(before, after, parent, snapshot)


def test_acknowledged_sampling_history_cannot_be_rewritten(native):
    before, after, parent, snapshot = transition(native)
    before["metadata"]["feature_stats"] = copy.deepcopy(
        after["metadata"]["feature_stats"]
    )
    stats = after["metadata"]["feature_stats"]["complexity"]
    stats["values"] += list(stats["values"])
    stats["values"][0] += 1
    stats["max"] += 1
    with pytest.raises(ValueError, match="rewrote"):
        validate_sampling_transition(before, after, parent, snapshot)


@pytest.mark.parametrize(
    "fault",
    ["bool_iteration", "bool_sequence", "stale_sequence", "wrong_hash", "host_failure"],
)
def test_sampling_ack_failure_poison_epoch_before_dispatch(native, fault):
    from unittest.mock import patch
    from uuid import uuid4

    from packages.openevolve_adapter import observer, sampling_control
    from packages.openevolve_adapter.checkpoint_native import state_sha256

    before, after, parent, snapshot = transition(native)
    epoch = uuid4()
    answer = {
        "iteration_grant": {
            "schema_version": "mineagent.openevolve-iteration-grant.v1",
            "epoch_id": str(epoch),
            "native_iteration": 1,
        },
        "sampling_receipt_id": str(uuid4()),
        "control_sequence": 2,
        "state_sha256": state_sha256(after),
    }
    if fault == "bool_iteration":
        answer["iteration_grant"]["native_iteration"] = True
    elif fault == "bool_sequence":
        answer["control_sequence"] = True
    elif fault == "stale_sequence":
        answer["control_sequence"] = 1
    elif fault == "wrong_hash":
        answer["state_sha256"] = "0" * 64
    with (
        observer.epoch_control(epoch),
        patch.object(
            sampling_control,
            "host_event",
            return_value=answer,
            side_effect=RuntimeError("uncertain") if fault == "host_failure" else None,
        ),
    ):
        with pytest.raises(observer.SearchControlAbort):
            sampling_control.before_submit(1, parent, [], snapshot, before, after)
        with pytest.raises(observer.SearchControlAbort):
            observer.require_trusted_population()
