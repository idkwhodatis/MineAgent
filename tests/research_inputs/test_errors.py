import pytest

from packages.research_inputs.errors import public_error_code


def test_unknown_legal_slug_never_leaks_as_public_error():
    assert public_error_code("external_secret_abc123") == "other_derivation_error"


@pytest.mark.parametrize(
    "value", ["invalid_json_or_mapping", "owner_cancelled", "worker_interrupted", None]
)
def test_finite_known_errors(value):
    assert public_error_code(value) == value


@pytest.mark.parametrize(
    "value", ["api-key=secret", "https://secret.test", "", "a" * 81, 42]
)
def test_unsafe_stored_diagnostics_rejected(value):
    with pytest.raises(ValueError):
        public_error_code(value)
