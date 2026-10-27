"""Retained source/run agreement, not physical dataset or epoch acceptance."""

import hashlib
from copy import deepcopy
from uuid import uuid4

import pytest

from backend.app.openevolve_backup_shape import EXTERNAL_FIELDS
from backend.app.openevolve_backup_spec import validate_specs
from packages.openevolve_adapter.checkpoint_native import canonical
from packages.openevolve_adapter.contracts import SearchSpec
from packages.research_runtime.broker_contracts import digest
from tests.openevolve.test_contracts import complete_spec


def case():
    config = complete_spec().model_dump(mode="json")
    code, manifest = "x = 1\n", {"name": "fixed"}
    for kind in ("original", "prepared"):
        config["preparation"][kind]["source_sha256"] = hashlib.sha256(
            code.encode()
        ).hexdigest()
        config["preparation"][kind]["manifest_sha256"] = digest(manifest)
    spec = SearchSpec.model_validate_json(canonical(config))
    row = dict.fromkeys(EXTERNAL_FIELDS["runs"].split())
    row.update(
        id=str(spec.run_id),
        owner_id=str(spec.owner_id),
        request_sha256=spec.sha256,
        config=config,
        purpose="strategy_search",
        engine="openevolve",
        dataset_version_id=str(spec.execution.dataset_version_id),
        strategy_version_id=str(spec.preparation.prepared.strategy_version_id),
        status="failed",
        generation=1,
    )
    saved = {
        "run_id": row["id"],
        "owner_id": row["owner_id"],
        "spec_sha256": spec.sha256,
        "spec": config,
        "original_strategy_id": row["strategy_version_id"],
        "prepared_strategy_id": row["strategy_version_id"],
        "original_source_sha256": spec.preparation.original.source_sha256,
        "prepared_source_sha256": spec.preparation.prepared.source_sha256,
        "dataset_id": row["dataset_version_id"],
        "dataset_sha256": spec.execution.dataset_sha256,
        "objective_sha256": digest(spec.objective),
        "execution_sha256": digest(spec.execution),
    }
    source = {
        "id": row["strategy_version_id"],
        "owner_id": row["owner_id"],
        "code": code,
        "manifest": manifest,
        "content_sha256": spec.preparation.prepared.source_sha256,
    }
    dataset = {
        "id": row["dataset_version_id"],
        "owner_id": row["owner_id"],
        "content_sha256": spec.execution.dataset_sha256,
    }
    closure = {
        "tables": {"strategy_search_specs": [saved]},
        "external_bindings": {
            "runs": [row],
            "strategy_versions": [source],
            "dataset_versions": [dataset],
        },
    }
    research = {
        "external_bindings": {
            "runs": [
                {
                    k: row[k]
                    for k in (
                        "id",
                        "owner_id",
                        "generation",
                        "purpose",
                        "engine",
                        "status",
                    )
                }
            ]
        },
        "tables": {
            "research_budget_controls": [
                {
                    "run_id": row["id"],
                    "owner_id": row["owner_id"],
                    "limits": spec.budget.model_dump(mode="json"),
                }
            ]
        },
    }
    return closure, research


def test_exact_persisted_source_and_run():
    closure, research = case()
    assert len(validate_specs(closure, research)) == 1


@pytest.mark.parametrize(
    "fault",
    [
        "source",
        "manifest",
        "dataset",
        "budget",
        "shared_status",
        "spec_sha",
        "resume",
        "extra_run",
        "extra_source",
        "missing_spec",
    ],
)
def test_exact_closed_identity_rejects_substitution(fault):
    closure, research = case()
    external = closure["external_bindings"]
    if fault == "source":
        external["strategy_versions"][0]["code"] += "# altered\n"
    elif fault == "manifest":
        external["strategy_versions"][0]["manifest"]["name"] = "other"
    elif fault == "dataset":
        external["dataset_versions"][0]["content_sha256"] = "0" * 64
    elif fault == "budget":
        research["tables"]["research_budget_controls"][0]["limits"]["calls"] += 1
    elif fault == "shared_status":
        research["external_bindings"]["runs"][0]["status"] = "succeeded"
    elif fault == "spec_sha":
        closure["tables"]["strategy_search_specs"][0]["spec_sha256"] = "0" * 64
    elif fault == "resume":
        external["runs"][0]["resume_checkpoint_id"] = str(uuid4())
    elif fault == "extra_run":
        external["runs"].append(deepcopy(external["runs"][0]))
    elif fault == "extra_source":
        extra = deepcopy(external["strategy_versions"][0])
        extra["id"] = str(uuid4())
        external["strategy_versions"].append(extra)
    else:
        closure["tables"]["strategy_search_specs"] = []
    with pytest.raises(ValueError):
        validate_specs(closure, research)
