"""Actual maintained prefix codecs in the full RD retained-source closure.

Relational rows are in-memory fixtures, not PostgreSQL/container acceptance.
"""

from uuid import UUID, uuid4

import pytest
import test_rdagent_source_backup as source_backup
from test_rdagent_source_backup import retained  # noqa: F401

from backend.app import rdagent_backup as backup
from packages.factors.contracts import ExposureHistory
from packages.rdagent_adapter.contracts import FactorResearchConfig
from packages.rdagent_adapter.host_evidence import research_snapshot_input
from workers.research.numeric_fixture import maintained_projection


@pytest.fixture(params=[(3, 10000), (10000, 16777216)])
def cutoff_closure(request, monkeypatch):
    rows, size = request.param
    original = source_backup.research_dict

    def configured():
        raw = original()
        raw["max_output_rows_per_cutoff"] = rows
        raw["max_output_bytes_per_cutoff"] = size
        return raw

    monkeypatch.setattr(source_backup, "research_dict", configured)
    data = request.getfixturevalue("retained")
    tables = data.closure["tables"]
    source = tables["factor_generation_sources"][0]
    config = FactorResearchConfig.model_validate(
        tables["factor_research_specs"][0]["config"]
    )
    code = next(
        obj
        for obj in data.factors["tables"]["factor_objects"]
        if obj["sha256"] == source["source_sha256"]
    )
    source_utf8 = (data.root / code["storage_key"]).read_bytes()
    snapshot = research_snapshot_input(
        config,
        factor_version_id=UUID(source["factor_version_id"]),
        code_sha256=source["source_sha256"],
        exposure_history=ExposureHistory(),
    )
    prefix, _ = maintained_projection(
        config,
        factor_version_id=UUID(source["factor_version_id"]),
        source_utf8=source_utf8,
        snapshot=snapshot,
    )
    scope = {k: source[k] for k in ("owner_id", "run_id", "attempt_id", "generation")}

    def row(table, **attrs):
        result = dict.fromkeys(backup.FIELDS[table].split())
        result.update(scope, id=str(uuid4()), created_at=source_backup.NOW, **attrs)
        tables[table].append(result)
        return result

    evaluation = row(
        "factor_evaluation_starts",
        start_id=source["start_id"],
        evaluated_source_id=source["id"],
        factor_version_id=source["factor_version_id"],
        snapshot_input=snapshot.model_dump(mode="json"),
        input_sha256=snapshot.sha256,
        expected_cutoffs=1,
    )
    admission_id = str(uuid4())
    cutoff = row(
        "factor_cutoff_executions",
        evaluation_id=evaluation["id"],
        start_id=source["start_id"],
        evaluated_source_id=source["id"],
        factor_version_id=source["factor_version_id"],
        cutoff_ordinal=0,
        candidate_admission_id=admission_id,
        projection=prefix.model_dump(mode="json"),
        projection_sha256=prefix.sha256,
    )
    data.research["tables"]["research_process_admissions"].append(
        dict(
            id=admission_id,
            **scope,
            role="factor_cutoff_candidate",
            logical_operation_id=cutoff["id"],
            image_id=config.runtime.candidate_image,
            profile=config.runtime.candidate_profile,
            ipc_policy="none",
            ticket_sha256=None,
        )
    )
    return data, cutoff, prefix


def test_real_maintained_projection_matches_full_source_closure(cutoff_closure):
    data, _, _ = cutoff_closure
    assert source_backup.check(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_output_rows", 4),
        ("max_output_bytes", 10001),
        ("calendar_sha256", "f" * 64),
        ("market_timezone", "UTC"),
        ("lookback_prior_sessions", 3),
        ("lag_sessions", 1),
        ("membership_sha256", "e" * 64),
    ],
)
def test_rehashed_prefix_still_rejects_frozen_input_drift(cutoff_closure, field, value):
    data, cutoff, prefix = cutoff_closure
    changed = prefix.model_copy(update={field: value})
    cutoff["projection"] = changed.model_dump(mode="json")
    cutoff["projection_sha256"] = changed.sha256
    with pytest.raises(ValueError):
        source_backup.check(data)


@pytest.mark.parametrize("field", ["max_output_rows", "max_output_bytes"])
@pytest.mark.parametrize("delta", [-1, 1])
def test_even_stricter_or_larger_rehashed_limits_cannot_reinterpret_config(
    cutoff_closure, field, delta
):
    data, cutoff, prefix = cutoff_closure
    changed = prefix.model_copy(update={field: getattr(prefix, field) + delta})
    cutoff["projection"] = changed.model_dump(mode="json")
    cutoff["projection_sha256"] = changed.sha256
    with pytest.raises(ValueError):
        source_backup.check(data)


@pytest.mark.parametrize(
    "field",
    [
        "input_fields",
        "preprocessing",
        "membership_available_at",
        "listings",
        "output_session",
    ],
)
def test_other_frozen_prefix_inputs_remain_bound(cutoff_closure, field):
    from datetime import timedelta

    from packages.factors.contracts import FittedPreprocessing

    data, cutoff, prefix = cutoff_closure
    if field == "input_fields":
        value = (prefix.input_fields[0].model_copy(update={"unit": "different"}),)
    elif field == "preprocessing":
        value = FittedPreprocessing(
            transform_id="different",
            code_sha256="a" * 64,
            parameters_sha256="b" * 64,
            fitted_artifact_sha256="c" * 64,
            fit_window=prefix.provided_window,
            last_input_at=prefix.host_cutoff,
            available_at=prefix.host_cutoff,
        )
    elif field == "membership_available_at":
        value = prefix.membership_available_at - timedelta(seconds=1)
    elif field == "listings":
        value = (
            prefix.listings[0].model_copy(update={"listing_id": "different"}),
            *prefix.listings[1:],
        )
    else:
        value = prefix.output_session + timedelta(days=1)
    changed = prefix.model_copy(update={field: value})
    cutoff["projection"] = changed.model_dump(mode="json")
    cutoff["projection_sha256"] = changed.sha256
    with pytest.raises(ValueError):
        source_backup.check(data)
