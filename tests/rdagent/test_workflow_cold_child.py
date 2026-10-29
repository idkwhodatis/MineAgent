"""Cold declaration checks from actual retained-source writer plus DB doubles."""

# ruff: noqa: F811
import json

import pytest
from test_engine_run_preflight import request_body  # noqa:F401
from test_rdagent_configured_source import retained  # noqa:F401
from test_rdagent_configured_source_service import setup_source  # noqa:F401
from test_rdagent_submission_service import queued_writer  # noqa:F401
from test_rdagent_workflow_submission import actual_graph, submit  # noqa:F401

from backend.app import research_workflow_backup as cold
from backend.app.factor_analysis_models import FactorAnalysisPlan
from backend.app.models import ResearchRun
from backend.app.rdagent_models import FactorResearchSpec
from packages.factors.contracts import canonical_bytes


def serialized(row):
    fields = (
        row.__table__.columns.keys() if hasattr(row, "__table__") else vars(row).keys()
    )
    return json.loads(
        json.dumps({key: getattr(row, key, None) for key in fields}, default=str)
    )


@pytest.mark.parametrize(
    "change",
    [
        None,
        "missing",
        "owner",
        "selection",
        "preflight",
        "runtime",
        "request",
        "snapshot",
        "analysis",
        "origin",
    ],
)
def test_cold_child_reconstructs_actual_writer_declaration(actual_graph, change):
    value = actual_graph
    receipt = submit(value)
    root, stage, context = value.root, value.stage, value.context
    own = {name: {} for name in cold.FIELDS}
    own["research_workflows"][str(root.id)] = serialized(root)
    own["research_workflow_stages"][str(stage.id)] = serialized(stage)
    revision = value.fixed[
        __import__(
            "backend.app.research_workflow_models",
            fromlist=["ResearchWorkflowRevision"],
        ).ResearchWorkflowRevision
    ][0]
    own["research_workflow_revisions"][str(revision.id)] = serialized(revision)
    row = serialized(receipt)
    own["factor_workflow_submissions"][row["id"]] = row
    metadata = {
        "indexes": own,
        "plans": {str(root.id): context.plan},
        "definitions": {str(stage.id): context.definition},
        "selections": {str(revision.id): context.selection},
        "checkpoints": {},
    }
    rows = value.q.session.rows
    rd = {
        "external_bindings": {
            "runs": [serialized(r) for r in rows if isinstance(r, ResearchRun)]
        },
        "tables": {
            "factor_research_specs": [
                serialized(r) for r in rows if isinstance(r, FactorResearchSpec)
            ]
        },
    }
    direct = {
        "tables": {
            "factor_analysis_plans": [
                serialized(r) for r in rows if isinstance(r, FactorAnalysisPlan)
            ]
        }
    }
    if change == "missing":
        own["factor_workflow_submissions"].clear()
    elif change == "owner":
        row["owner_id"] = "00000000-0000-0000-0000-000000000001"
    elif change in {"selection", "preflight", "runtime"}:
        row[
            {
                "selection": "selection_sha256",
                "preflight": "preflight_sha256",
                "runtime": "runtime_profile_sha256",
            }[change]
        ] = "0" * 64
    elif change == "request":
        row["request_canonical"] = "{}"
    elif change == "snapshot":
        rd["tables"]["factor_research_specs"][0]["input_sha256"] = "0" * 64
    elif change == "analysis":
        direct["tables"]["factor_analysis_plans"][0]["objective_canonical"] = "{}"
    elif change == "origin":
        row["submission_origin"] = "human"
    if change is None:
        result = cold.validate_child_declarations(
            metadata, rd_source_closure=rd, direct_analysis_closure=direct
        )
        assert set(result) == {str(receipt.run_id)}
        assert (
            canonical_bytes(result[str(receipt.run_id)]).decode()
            == canonical_bytes(rd["external_bindings"]["runs"][0]["config"]).decode()
        )
    else:
        with pytest.raises(ValueError):
            cold.validate_child_declarations(
                metadata, rd_source_closure=rd, direct_analysis_closure=direct
            )
