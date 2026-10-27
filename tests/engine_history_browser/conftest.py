"""Reuse actual disposable schema/accounts; never manufacture UI JSON."""

import os

import pytest

from tests.integration import conftest as integration
from tests.integration.conftest import (  # noqa: F401
    make_account,
    migrated_engine,
    other,
    owner,
    postgres_engine,
    settings,
)


@pytest.fixture(scope="session")
def postgres_url():
    if not os.environ.get("TEST_DATABASE_URL"):
        pytest.fail(
            "Real engine-history browser gate requires TEST_DATABASE_URL; skipping is not acceptance",
            pytrace=False,
        )
    return integration.postgres_url.__wrapped__()
