"""Maintained provider identity, not process/snapshot authority."""

from copy import deepcopy

import pytest
from test_rdagent_contracts import research_dict

from backend.app.rdagent_complete_evaluation import maintained_calendar
from backend.app.services import DomainError
from packages.rdagent_adapter.contracts import FactorResearchConfig


def test_complete_fixture_calendar_and_single_declared_day():
    data = research_dict()
    config = FactorResearchConfig.model_validate(data)
    calendar = maintained_calendar(config)
    data["input"]["window"] = {"start": "2024-03-15", "end": "2024-03-16"}
    one = FactorResearchConfig.model_validate(data)
    assert maintained_calendar(one) == calendar
    assert (
        len(
            [
                s
                for s in calendar.sessions
                if one.input.window.start <= s.session_date < one.input.window.end
            ]
        )
        == 1
    )


@pytest.mark.parametrize(
    "field", ["content_sha256", "input_files_sha256", "availability_revision_sha256"]
)
def test_dataset_claim_is_not_actual_maintained_bytes(field):
    data = deepcopy(research_dict())
    data["input"]["dataset"][field] = "a" * 64
    with pytest.raises(DomainError):
        maintained_calendar(FactorResearchConfig.model_validate(data))
