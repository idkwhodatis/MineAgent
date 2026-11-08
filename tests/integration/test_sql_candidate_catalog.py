"""Opt-in metadata evidence from a pristine disposable 032f predecessor only."""

import os

import pytest

from script.sql_candidate_catalog import TABLES, export

pytestmark = pytest.mark.postgres


def test_pristine_predecessor_catalog_before_any_row_candidate(migrated_engine):
    target = os.environ.get("MINEAGENT_SQL_CANDIDATE_CATALOG_DIR")
    if not target:
        pytest.skip("Dedicated isolated candidate metadata export is not enabled")
    with migrated_engine.begin() as connection:
        catalog = export(connection, target)
    assert len(catalog["relations"]) == len(TABLES) == 8
    checks = [row for row in catalog["constraints"] if row["contype"] == "c"]
    assert checks and all(row["conenforced"] and row["convalidated"] for row in checks)
