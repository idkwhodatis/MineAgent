from uuid import UUID

import pytest

from packages.rdagent_adapter.checkpoint import FactorCheckpoint


def checkpoint():
    return {
        "config_sha256": "a" * 64,
        "input_sha256": "b" * 64,
        "completed": [
            {
                "logical_step": 0,
                "start_id": str(UUID(int=1)),
                "outcome_id": str(UUID(int=2)),
                "factor_version_id": str(UUID(int=3)),
                "evaluated_source_id": str(UUID(int=30)),
                "adopted_source_id": None,
                "adopted_factor_version_id": None,
                "continuation_source_id": str(UUID(int=30)),
                "continuation_factor_version_id": str(UUID(int=3)),
                "adoption_receipt_sha256": "c" * 64,
                "outcome": "valid_unadopted",
            }
        ],
        "next_step": 1,
    }


def test_checkpoint_is_platform_json_not_native_pickle():
    value = FactorCheckpoint.model_validate(checkpoint())
    assert FactorCheckpoint.model_validate_json(value.model_dump_json()) == value
    raw = checkpoint()
    raw["pickle"] = "opaque"
    with pytest.raises(ValueError):
        FactorCheckpoint.model_validate(raw)


@pytest.mark.parametrize("change", ["gap", "bool", "alias", "terminal"])
def test_checkpoint_rejects_incomplete_or_impossible_history(change):
    raw = checkpoint()
    if change == "gap":
        raw["next_step"] = 2
    elif change == "bool":
        raw["next_step"] = True
    else:
        row = {**raw["completed"][0], "logical_step": 1}
        raw["completed"].append(row)
        raw["next_step"] = 2
        if change == "terminal":
            raw["completed"][0]["outcome"] = "accepted"
            row["start_id"] = str(UUID(int=4))
            row["outcome_id"] = str(UUID(int=5))
    with pytest.raises(ValueError):
        FactorCheckpoint.model_validate(raw)


@pytest.mark.parametrize(
    "field",
    ["evaluated_source_id", "continuation_source_id", "adoption_receipt_sha256"],
)
def test_checkpoint_cannot_discard_evaluated_continuation_or_adoption_evidence(field):
    raw = checkpoint()
    raw["completed"][0][field] = None
    with pytest.raises(ValueError):
        FactorCheckpoint.model_validate(raw)


def test_checkpoint_keeps_fallback_separate_from_repair_workspace():
    raw = checkpoint()
    raw["completed"][0]["adopted_source_id"] = str(UUID(int=40))
    raw["completed"][0]["adopted_factor_version_id"] = str(UUID(int=4))
    parsed = FactorCheckpoint.model_validate(raw)
    assert (
        parsed.completed[0].adopted_source_id
        != parsed.completed[0].continuation_source_id
    )
