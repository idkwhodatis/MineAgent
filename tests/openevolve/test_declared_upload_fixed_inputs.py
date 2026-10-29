import pytest
from pydantic import ValidationError

from packages.openevolve_structured.provider_run import FixedExecutionInputs
from tests.openevolve.test_structured_persisted_identity import graph


def legacy():
    return graph()[1].config["fixed_inputs"]


def test_old_fixed_input_bytes_do_not_acquire_new_qualification():
    old = legacy()
    assert old["schema_version"] == "mineagent.oe-structured-retained-inputs.v1"
    assert old["market_qualification"] == "synthetic_by_construction"
    assert (
        FixedExecutionInputs.model_validate_json(
            __import__("json").dumps(old)
        ).model_dump(mode="json")
        == old
    )


@pytest.mark.parametrize(
    "version,quality,valid",
    [
        (
            "mineagent.oe-structured-retained-inputs.v1",
            "synthetic_by_construction",
            True,
        ),
        (
            "mineagent.oe-structured-retained-inputs.v2",
            "user_declaration_unverified",
            True,
        ),
        (
            "mineagent.oe-structured-retained-inputs.v1",
            "user_declaration_unverified",
            False,
        ),
        (
            "mineagent.oe-structured-retained-inputs.v2",
            "synthetic_by_construction",
            False,
        ),
        (
            "mineagent.oe-structured-retained-inputs.v3",
            "user_declaration_unverified",
            False,
        ),
        ("mineagent.oe-structured-retained-inputs.v2", "verified", False),
    ],
)
def test_qualification_is_an_explicit_version_pair(version, quality, valid):
    import json

    raw = legacy() | {"schema_version": version, "market_qualification": quality}
    if valid:
        assert (
            FixedExecutionInputs.model_validate_json(
                json.dumps(raw)
            ).market_qualification
            == quality
        )
    else:
        with pytest.raises(ValidationError):
            FixedExecutionInputs.model_validate_json(json.dumps(raw))
