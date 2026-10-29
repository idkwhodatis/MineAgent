"""Normal operator settings are explicit and closed by default."""

import pytest

from backend.app.core.config import Settings
from backend.app.oe_structured_server_profile import resolve_runtime
from backend.app.services import DomainError


def settings(**kwargs):
    return Settings(
        database_url="postgresql://test:test@localhost/test",
        secret_key="x" * 32,
        _env_file=None,
        **kwargs,
    )


def test_default_settings_never_enable_normal_search_or_provider():
    value = settings()
    assert not value.oe_structured_research_enabled
    assert not value.oe_structured_live_provider_enabled
    assert not value.oe_structured_synthetic_provider_enabled
    assert value.oe_structured_provider_profiles == ()
    assert value.oe_structured_runtime_profile is None
    assert value.oe_structured_provider_python is None
    with pytest.raises(DomainError) as error:
        resolve_runtime(value)
    assert error.value.code == "oe_submission_disabled"


@pytest.mark.parametrize("path", ["python", "./python", "", "/bad\x00path"])
def test_sdk_interpreter_must_be_explicit_absolute_path(path):
    with pytest.raises(ValueError):
        settings(oe_structured_provider_python=path)


def test_enable_flag_alone_does_not_supply_runtime_or_sdk():
    with pytest.raises(DomainError) as error:
        resolve_runtime(settings(oe_structured_research_enabled=True))
    assert error.value.code == "oe_runtime_unconfigured"


def test_public_profile_model_does_not_import_service_runtime():
    from backend.app.oe_structured_server_profile import (
        OEStructuredServerProfile as alias,
    )
    from backend.app.oe_structured_server_schemas import OEStructuredServerProfile

    assert alias is OEStructuredServerProfile
