"""Pure cold parent/request joins with real retained files, no launch authority."""

import json
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app.research_workflow_backup import read_seed_parent
from packages.factors.contracts import digest
from packages.rdagent_adapter.seed_parent import SeedParentReference
from tests.rdagent.test_rdagent_seed_parent import retained_parent as retained_parent  # noqa: PLC0414


@pytest.mark.parametrize("fault", [None, "bytes", "scope", "reference", "size"])
def test_actual_parent_object_and_declaration(retained_parent, fault):
    r = retained_parent
    reference = SeedParentReference(
        owner_id=r.owner,
        factor_id=r.factor.id,
        factor_version_id=r.version.id,
        ordinal=1,
        code_object_id=r.obj.id,
        code_sha256=r.obj.sha256,
        code_size_bytes=r.obj.size_bytes,
        definition_sha256=r.version.definition_sha256,
        source_provenance_sha256=digest(r.version.source_provenance),
    )
    tables = {
        key: [json.loads(json.dumps(vars(row), default=str))]
        for key, row in (
            ("factors", r.factor),
            ("factor_versions", r.version),
            ("factor_objects", r.obj),
        )
    }
    if fault == "bytes":
        r.path.write_bytes(b"changed source")
    elif fault == "scope":
        tables["factor_versions"][0]["owner_id"] = str(uuid4())
    elif fault == "reference":
        reference = reference.model_copy(update={"ordinal": 2})
    elif fault == "size":
        tables["factor_objects"][0]["size_bytes"] = True
    calls = []

    def read(key, sha, maximum):
        calls.append((key, sha, maximum))
        return (r.settings.storage_root / key).read_bytes()

    config = NS(workflow_parent=reference)
    if fault:
        with pytest.raises(ValueError):
            read_seed_parent(config, factor_closure={"tables": tables}, read_bytes=read)
    else:
        actual = read_seed_parent(
            config, factor_closure={"tables": tables}, read_bytes=read
        )
        assert actual.reference == reference and actual.source == r.path.read_text()
        assert calls == [(r.obj.storage_key, r.obj.sha256, 131072)]


@pytest.mark.parametrize(
    "fault", [None, "missing_row", "extra_row", "prompt", "bytes", "later_call"]
)
@pytest.mark.parametrize("layout", [None, "generation_user.v1"])
def test_actual_first_request_reverse_inventory_and_bytes(tmp_path, fault, layout):
    from backend.app.research_workflow_backup import validate_consumption
    from packages.rdagent_adapter.workflow_prompt import first_workflow_messages
    from packages.rdagent_provider.codec import canonical_bytes, request_sha256
    from packages.rdagent_provider.contracts import (
        ProviderMessage,
        RDProviderChatRequest,
    )
    from tests.rdagent.test_rdagent_workflow_child_contracts import (
        config as make_config,
    )

    config = make_config()
    if layout is not None:
        config = type(config).model_validate(
            {**config.model_dump(mode="json"), "workflow_prompt_layout": layout}
        )
    snapshot = config.workflow_inputs
    scope = {
        "owner_id": str(snapshot.binding.owner_id),
        "run_id": str(config.workflow_child_run_id),
        "attempt_id": str(uuid4()),
        "generation": 1,
    }
    operation_id, transport_id, call_id, object_id = [str(uuid4()) for _ in range(4)]
    system, user = first_workflow_messages(config)
    if fault == "prompt":
        system = "Replaced original frozen guidance"
    request = RDProviderChatRequest(
        model_profile_id=config.model_binding.profile_id,
        model_binding_sha256=config.model_binding.sha256,
        model=config.model_binding.chat_model,
        messages=(
            ProviderMessage(role="system", content=system),
            ProviderMessage(role="user", content=user),
        ),
        max_output_tokens=config.model_binding.max_output_tokens,
        response_format=None,
    )
    raw = canonical_bytes(request.schema_version, request)
    sha = request_sha256(request.schema_version, request)
    path = tmp_path / "request.json"
    path.write_bytes(raw if fault != "bytes" else b"replaced")
    operation = {
        **scope,
        "id": operation_id,
        "protocol": request.schema_version,
        "request": request.model_dump(mode="json"),
        "request_sha256": sha,
        "request_object_id": object_id,
    }
    member = {
        **scope,
        "operation_id": operation_id,
        "call_id": call_id,
        "request_ordinal": 0,
        "relation": "initial",
    }
    call = {
        **scope,
        "id": call_id,
        "phase": "generation",
        "ordinal": 1 if fault == "later_call" else 0,
    }
    transport = {
        **scope,
        "id": transport_id,
        "operation_id": operation_id,
        "ordinal": 1,
    }
    row = {
        **scope,
        "stage_id": str(snapshot.binding.stage_id),
        "workflow_id": str(snapshot.binding.workflow_id),
        "input_revision_id": str(snapshot.selection.revision_id),
        "input_sha256": config.workflow_inputs_sha256,
        "operation_id": operation_id,
        "transport_id": transport_id,
        "request_sha256": sha,
    }
    rows = {} if fault == "missing_row" else {str(uuid4()): row}
    if fault == "extra_row":
        rows[str(uuid4())] = dict(row)
    metadata = {
        "indexes": {
            "research_workflow_model_consumption": rows,
            "research_workflow_stages": {
                row["stage_id"]: {"child_run_id": scope["run_id"]}
            },
        }
    }
    kwargs = {
        "configs": {scope["run_id"]: config},
        "research_closure": {
            "tables": {
                "research_model_operations": [operation],
                "research_model_transports": [transport],
                "research_objects": [
                    {
                        "id": object_id,
                        "owner_id": scope["owner_id"],
                        "sha256": sha,
                        "size_bytes": len(raw),
                        "storage_key": "request.json",
                    }
                ],
            }
        },
        "rd_source_closure": {
            "tables": {
                "factor_upstream_calls": [call],
                "factor_generation_model_operations": [member],
            }
        },
        "factor_closure": {},
        "read_bytes": lambda key, sha, maximum: (tmp_path / key).read_bytes(),
    }
    if fault:
        with pytest.raises(ValueError):
            validate_consumption(metadata, **kwargs)
    else:
        validate_consumption(metadata, **kwargs)


def test_top_level_never_accepts_old_physical_tuple():
    from backend.app.research_workflow_backup import validate_closure

    with pytest.raises(ValueError, match="exact successor tuple"):
        validate_closure(
            {},
            research_closure={
                "schema_version": "mineagent.research-restore-closure.v13"
            },
            rd_source_closure={},
            execution_closure={},
            direct_analysis_closure={},
            factor_closure={},
            input_closure={},
            steer_closure={},
            read_bytes=lambda *_: pytest.fail("must not read"),
            inventory=[],
        )


@pytest.mark.parametrize(
    "schema",
    [
        "mineagent.rd-source-restore-closure.v8",
        "mineagent.rd-source-restore-closure.v9",
    ],
)
def test_prephysical_context_explicit_schema_and_empty_inventory(schema):
    from backend.app import research_workflow_backup as cold
    from tests.research_workflow.test_workflow_backup import empty

    kwargs = {
        "rd_source_closure": {
            "schema_version": schema,
            "external_bindings": {"runs": []},
            "tables": {"factor_research_specs": []},
        },
        "direct_analysis_closure": {"tables": {"factor_analysis_plans": []}},
        "factor_closure": {},
        "read_bytes": lambda *_: pytest.fail("empty graph reads no bytes"),
    }
    if schema.endswith(".v8"):
        with pytest.raises(
            ValueError,
            match="Workflow pre-physical context requires exact supported RD schema",
        ):
            cold.build_workflow_context(empty(), **kwargs)
    else:
        assert cold.build_workflow_context(empty(), **kwargs) == {}
