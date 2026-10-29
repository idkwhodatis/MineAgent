"""Actual initializer declaration boundary, with explicit SQL/lock doubles."""

from contextlib import contextmanager
from types import SimpleNamespace as NS

import pytest

from packages.research_runtime.process_contracts import ProcessError
from tests.structured.test_services import material
from workers.research import structured_factor as worker


@pytest.mark.parametrize(
    "revision,allowed",
    [
        ("025_structured_factor_strategy", True),
        ("026_rd_seeded_submissions", True),
        ("028_oe_structured_search", True),
        ("029_research_workflow", True),
        ("024_openevolve_initial", False),
        ("029_unknown", False),
    ],
)
def test_only_exact_inherited_pair_revisions_reach_owned_dataset_validation(
    tmp_path, monkeypatch, revision, allowed
):
    values = material()
    config = values["config"].model_dump(mode="json")
    config["engine_dependency_lock_sha256"] = worker.dependency_lock_sha256()
    reads = []

    class ReachedDataset(Exception):
        pass

    class Session:
        def scalars(self, query):
            assert str(query) == "SELECT version_num FROM alembic_version"
            return NS(all=lambda: [revision])

        def get(self, model, key):
            assert model is worker.DatasetVersion
            reads.append(key)
            raise ReachedDataset

        def add(self, row):
            pytest.fail("declaration written before owned dataset validation")

    @contextmanager
    def begin():
        yield Session()

    monkeypatch.setattr(worker, "_try_neutral_lock", lambda *a: None)
    with pytest.raises(ReachedDataset if allowed else ProcessError) as caught:
        worker.create_structured_acceptance_run.__wrapped__(
            NS(begin=begin),
            owner_id=values["fence"].owner_id,
            program=values["program"],
            config=config,
            settings=NS(storage_root=tmp_path),
        )
    assert bool(reads) is allowed
    if not allowed:
        assert str(caught.value) == "structured_schema_unsupported"
