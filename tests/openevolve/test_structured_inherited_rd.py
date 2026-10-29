"""Explicit inherited protocol revision boundaries; not migration activation."""

from types import SimpleNamespace as NS
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from backend.app.rdagent_snapshot_config import provider_config_in_session
from packages.research_runtime.process_contracts import ProcessError
from workers.research.rdagent_numeric import require_configured_revision
from workers.research.rdagent_operational import require_maintained_revision
from workers.research.rdagent_seed_queue import seeded_row


@pytest.mark.parametrize(
    "revision",
    ["026_rd_seeded_submissions", "028_oe_structured_search", "029_research_workflow"],
)
def test_exact_predecessor_and_successor_retain_protocols(revision):
    session = MagicMock()
    session.scalars.return_value.all.return_value = [revision]
    assert provider_config_in_session(session) is True
    require_configured_revision(session)
    require_maintained_revision(session)
    row = object()
    session.scalar.return_value = row
    assert seeded_row(session, NS(owner_id=uuid4(), id=uuid4()), [revision]) is row


@pytest.mark.parametrize(
    "versions",
    [
        [],
        ["028_unknown"],
        ["028_oe_structured_search", "026_rd_seeded_submissions"],
        ["027_unknown"],
    ],
)
def test_no_unknown_future_or_ambiguous_revision_admission(versions):
    session = MagicMock()
    session.scalars.return_value.all.return_value = versions
    with pytest.raises(ValueError):
        provider_config_in_session(session)
    for guard in (require_configured_revision, require_maintained_revision):
        with pytest.raises(ProcessError):
            guard(session)
    assert seeded_row(session, NS(owner_id=uuid4(), id=uuid4()), versions) is None
    session.scalar.assert_not_called()
