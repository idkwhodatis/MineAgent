"""Normal source-family boundary precedes any snapshot values; DB is doubled."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import oe_structured_provider_input as submission
from backend.app import oe_structured_source_qualification as source
from backend.app.services import DomainError
from tests.openevolve.test_structured_provider_submission import request


@pytest.mark.parametrize(
    "schema", ["mineagent.factor-research.v1", "mineagent.factor-research.v2"]
)
def test_existing_source_families_remain_qualified(schema):
    source.require_source_config({"schema_version": schema})


@pytest.mark.parametrize(
    "config",
    [
        None,
        {},
        {"schema_version": "mineagent.factor-research.v3"},
        {"schema_version": "mineagent.factor-research.v4"},
        {"schema_version": False},
    ],
)
def test_workflow_and_unknown_sources_are_not_qualified_by_dto(config):
    with pytest.raises(DomainError, match="qualified predecessor"):
        source.require_source_config(config)


def test_workflow_source_rejected_before_snapshot_values_even_with_release(monkeypatch):
    declaration = request()
    publication = NS(id=declaration.snapshot_id, run_id=uuid4())
    run = NS(
        purpose="factor_research",
        engine="rdagent",
        config={"schema_version": "mineagent.factor-research.v3"},
    )
    rows = iter((publication, NS(id=uuid4()), run))
    monkeypatch.setattr(submission, "_try_neutral_lock", lambda *a: None)
    monkeypatch.setattr(
        submission,
        "read_snapshot_in_session",
        lambda *a: pytest.fail("unqualified values read"),
    )
    monkeypatch.setattr(
        submission,
        "native_storage_guard",
        lambda *a: pytest.fail("unqualified physical guard"),
    )
    with pytest.raises(DomainError) as caught:
        submission.resolve_submission_data(
            NS(scalar=lambda _: next(rows)), uuid4(), declaration, NS()
        )
    assert caught.value.code == "structured_source_family_unsupported"


def test_workflow_source_preflight_never_calls_disclosure(monkeypatch):
    from contextlib import contextmanager

    from backend.app import oe_structured_preflight_data as preflight
    from tests.openevolve.test_structured_submission import request as old_request

    rows = iter(
        (
            NS(run_id=uuid4()),
            NS(
                purpose="factor_research",
                engine="rdagent",
                config={"schema_version": "mineagent.factor-research.v3"},
            ),
        )
    )

    @contextmanager
    def factory():
        yield NS(scalar=lambda _: next(rows))

    monkeypatch.setattr(
        preflight, "disclose", lambda *a, **kw: pytest.fail("unqualified disclose")
    )
    with pytest.raises(DomainError) as caught:
        preflight.resolve_preflight_data(factory, uuid4(), old_request(), NS())
    assert caught.value.code == "structured_source_family_unsupported"


@pytest.mark.parametrize(
    "revisions,available",
    [
        (["028_oe_structured_search"], True),
        (["029_research_workflow"], True),
        (["026_rd_seeded_submissions"], False),
        (["029_future"], False),
        ([], False),
        (["028_oe_structured_search", "029_research_workflow"], False),
    ],
)
def test_normal_public_capability_has_exact_successor_allowlist(revisions, available):
    from backend.app.oe_structured_routes import capabilities

    result = capabilities(NS(), NS(scalars=lambda _: NS(all=lambda: revisions)))
    assert result.routes_available is available
    assert result.can_preflight is available
