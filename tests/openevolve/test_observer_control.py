import pickle
from uuid import uuid4

import pytest

from packages.openevolve_adapter.observer import (
    SearchControlAbort,
    abort_control,
    control_guard,
    epoch_control,
    require_trusted_population,
)
from packages.research_runtime.control import BrokerAbort


def test_partial_population_failure_is_terminal_and_cannot_checkpoint():
    calls = []
    with epoch_control(uuid4()) as state:
        with pytest.raises(SearchControlAbort):
            try:
                with control_guard("result_application"):
                    calls.append("native_insert")
                    raise RuntimeError("private detail")
            except Exception:  # noqa: BLE001 - emulate upstream candidate-error fallback
                calls.append("upstream_fallback")
            calls.append("replacement_submission")
        assert not state.population_state_trusted
        with pytest.raises(SearchControlAbort):
            require_trusted_population()
    assert calls == ["native_insert"]


def test_terminal_broker_abort_is_not_mislabeled_candidate_failure():
    with pytest.raises(BrokerAbort) as result, control_guard("result_application"):
        raise BrokerAbort("fence_lost")
    assert result.value.code == "fence_lost"


def test_abort_pickles_without_exception_payload():
    with pytest.raises(SearchControlAbort) as result:
        abort_control("submission", RuntimeError("private-token"))
    serialized = pickle.dumps(result.value)
    assert b"private-token" not in serialized
    restored = pickle.loads(serialized)
    assert restored.phase == "submission" and not isinstance(restored, Exception)


def test_checkpoint_requires_bound_trusted_epoch():
    with pytest.raises(SearchControlAbort):
        require_trusted_population()
    identifier = uuid4()
    with epoch_control(identifier):
        assert require_trusted_population() == identifier
        with pytest.raises(ValueError), epoch_control(uuid4()):
            pass


@pytest.mark.parametrize(
    "signal", [BrokerAbort("fence_lost"), KeyboardInterrupt(), SystemExit(2)]
)
def test_terminal_signal_poisoning_preserves_original_exception(signal):
    with epoch_control(uuid4()) as state:
        with pytest.raises(type(signal)) as caught, control_guard("result_application"):
            raise signal
        assert caught.value is signal
        assert state.population_state_trusted is False
        with pytest.raises(SearchControlAbort):
            require_trusted_population()


@pytest.mark.parametrize(
    "name",
    [
        "workers",
        "islands",
        "top_programs",
        "diverse_programs",
        "model_calls_per_intent",
    ],
)
def test_runtime_profile_rejects_boolean_numeric_literals(name):
    from pydantic import ValidationError

    from packages.openevolve_adapter.runtime_profile import PROFILE, RuntimeProfile

    value = PROFILE.model_dump(mode="json")
    value[name] = True
    with pytest.raises(ValidationError):
        RuntimeProfile.model_validate(value)
