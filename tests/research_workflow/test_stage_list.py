from datetime import UTC, datetime
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import research_workflow_stage_list as service
from backend.app.services import DomainError


@pytest.mark.parametrize("change", [None, "foreign", "order", "missing", "bad_limit"])
def test_stage_navigation_is_owner_scoped_bounded_metadata(monkeypatch, change):
    owner, workflow = uuid4(), uuid4()
    root = NS(owner_id=owner, id=workflow)
    rows = [
        NS(
            owner_id=owner,
            workflow_id=workflow,
            id=uuid4(),
            ordinal=i,
            kind="rd_factor_generation",
            input_revision_id=uuid4(),
            definition_sha256="a" * 64,
            created_at=datetime.now(UTC),
        )
        for i in (1, 2)
    ]
    if change == "foreign":
        rows[-1].owner_id = uuid4()
    elif change == "order":
        rows[-1].ordinal = 1
    elif change == "missing":
        root = None
    session = NS(scalar=lambda q: root, scalars=lambda q: rows)
    monkeypatch.setattr(service, "require_schema", lambda s: None)
    if change:
        with pytest.raises(DomainError):
            service.read_stage_list(
                session, owner, workflow, limit=0 if change == "bad_limit" else 1
            )
    else:
        result = service.read_stage_list(session, owner, workflow, limit=1)
        assert result.has_more and len(result.items) == 1
        assert "status" not in result.items[0].model_dump()
