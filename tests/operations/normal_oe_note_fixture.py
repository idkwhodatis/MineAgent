"""Actual original codecs, engine loop and retained bytes; broker/kernel are doubles.

This is not a complete OE controller graph or PostgreSQL/container acceptance.
Derived from the existing test_structured_backup engine-byte fixture unchanged.
"""

import hashlib
from uuid import uuid4

from backend.app import oe_structured_backup as service
from packages.factors.contracts import digest
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.process_persistence_codec import encode_row
from tests.openevolve.test_structured_backup import execution_material


def actual_engine_material(retained_declaration, tmp_path):
    """Real retained codecs/files; broker/accounting and stop observations are doubles."""
    from uuid import UUID

    from packages.factors.contracts import canonical_bytes
    from packages.openevolve_structured.objective import (
        StructuredObjective,
        project_result,
    )
    from packages.structured_factor_strategy.operator import ClosedExchange
    from packages.structured_factor_strategy.wire import (
        StructuredResult,
        accounting_only,
        read_result,
        write_input,
    )
    from runtimes.portable_engine.export import export_result
    from runtimes.portable_engine.runtime import Controller, load_inputs
    from runtimes.structured_factor_engine.journal import StructuredInputBinding
    from tests.portable.test_engine import FakeAQ, FakeBroker, fake_result
    from tests.structured_factor_strategy.test_process_journal import records

    args, retained, value, config, execution, candidate = execution_material(
        retained_declaration
    )
    _, market = service.reconstruct_execution(
        execution,
        candidate,
        config,
        args["publication"],
        args["dataset"],
        args["read_bytes"],
    )
    strategy = write_input(value)
    native = load_inputs(
        canonical_bytes(value.engine), market, calendar_verifier=lambda *_: None
    )  # Explicit calendar double; not native certification.
    exchange = ClosedExchange(value)
    controller = Controller(native, exchange)
    broker = FakeBroker(native.sessions[0])
    for session in native.sessions:
        broker.session = session
        controller.on_session(session, broker, FakeAQ)
    result = StructuredResult(
        execution_id=value.execution_id,
        structured_strategy_version_id=value.structured_strategy_version_id,
        snapshot_id=value.snapshot.snapshot_id,
        strategy_input_sha256=hashlib.sha256(strategy).hexdigest(),
        market_sha256=value.market_sha256,
        decisions=tuple(exchange.records),
        accounting_tables=accounting_only(
            export_result(fake_result(controller, broker), controller)
        ),
    )
    raw = canonical_bytes(result)
    assert read_result(raw, value) == result
    config.search.objective = StructuredObjective(
        window=value.engine.window, initial_cash=value.engine.initial_cash
    )
    measurement = project_result(strategy, raw, config.search.objective)
    launch, identity, stop, extraction = records()
    binding = StructuredInputBinding(
        execution_id=value.execution_id,
        snapshot_id=value.snapshot.snapshot_id,
        structured_strategy_version_id=value.structured_strategy_version_id,
        strategy_input_sha256=hashlib.sha256(strategy).hexdigest(),
        strategy_input_size_bytes=len(strategy),
        market_sha256=value.market_sha256,
        market_size_bytes=len(market),
    )
    admission = launch.intent.admission.model_copy(
        update={
            **{k: UUID(execution[k]) for k in ("owner_id", "run_id", "attempt_id")},
            "logical_operation_id": value.execution_id,
            "image_id": config.runtime.engine_image_id,
            "input_sha256": digest(binding),
        }
    )
    intent = launch.intent.model_copy(
        update={"admission": admission, "inputs": binding}
    )
    launch = launch.model_copy(update={"intent": intent})
    identity = identity.model_copy(
        update={
            "admission_sha256": digest(admission),
            "launch_sha256": digest(launch),
            "image_id": admission.image_id,
        }
    )
    stop = stop.model_copy(
        update={"launch_sha256": digest(launch), "identity_sha256": digest(identity)}
    )
    extraction = extraction.model_copy(
        update={
            "launch_sha256": digest(launch),
            "identity_sha256": digest(identity),
            "strategy_input_sha256": binding.strategy_input_sha256,
            "market_sha256": binding.market_sha256,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
        }
    )
    a = {
        k: str(v) if hasattr(v, "hex") else v for k, v in encode_row(admission).items()
    }
    scope = {k: a[k] for k in service.SCOPE}
    held = {
        **scope,
        "id": a["reservation_id"],
        "state": "settled",
        "purpose": "process_unit",
        "logical_key": "structured-factor-execution:" + execution["id"],
        "amounts": BudgetVector(
            evaluator_executions=1,
            process_slots=1,
            wall_milliseconds=90000,
            storage_bytes=268500992,
        ).model_dump(),
    }
    shared = {
        "research_process_admissions": {a["id"]: a},
        "research_budget_reservations": {held["id"]: held},
        "research_budget_settlements": {
            "settled": {
                **scope,
                "reservation_id": held["id"],
                "charge": held["amounts"],
                "outcome": "conservative_unknown",
            }
        },
        "research_process_states": {a["id"]: {**scope, "status": "stopped"}},
        "research_process_receipts": {},
    }
    outcome = {
        **scope,
        "execution_id": execution["id"],
        "candidate_id": candidate["id"],
        "admission_id": a["id"],
        "result_canonical": raw.decode(),
        "result_sha256": hashlib.sha256(raw).hexdigest(),
        "result_size_bytes": len(raw),
        "measurement_canonical": canonical_bytes(measurement).decode(),
        "measurement_sha256": digest(measurement),
        "objective_sha256": digest(config.search.objective),
    }
    prefix = f"research-processes/{a['owner_id']}/{a['id']}/"
    retained = dict(retained)
    for kind, name, document, payload in (
        ("launch_intent", "launch", launch, intent),
        ("launch_identity", "identity", identity, identity),
        ("stop", "stop", stop, stop),
        ("extraction", "extraction", extraction, extraction),
    ):
        rid = str(uuid4())
        shared["research_process_receipts"][rid] = {
            **scope,
            "id": rid,
            "admission_id": a["id"],
            "kind": kind,
            "payload": payload.model_dump(mode="json"),
            "payload_sha256": digest(payload),
        }
        retained[prefix + "control/" + name + ".json"] = canonical_bytes(document)
        if kind in {"stop", "extraction"}:
            outcome.update(
                {
                    kind + "_receipt_id": rid,
                    kind + "_receipt_kind": kind,
                    kind + "_receipt_sha256": digest(payload),
                }
            )
    retained[prefix + "output/result.json"] = raw
    physical = {}
    for key, content in retained.items():
        path = tmp_path / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        physical[key] = {
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
        }

    def read(key, sha, size):
        return (tmp_path / key).read_bytes()

    assert (
        service.validate_engine_outcome(
            outcome,
            execution,
            candidate,
            config,
            args["publication"],
            args["dataset"],
            shared,
            physical,
            read,
        )
        == measurement
    )
    return {
        "args": args,
        "retained": retained,
        "value": value,
        "config": config,
        "execution": execution,
        "candidate": candidate,
        "outcome": outcome,
        "measurement": measurement,
        "shared": shared,
        "physical": physical,
        "read": read,
        "result": result,
    }


def typed_config(material):
    """Real complete config codec, not a fabricated persisted/provider graph."""
    from packages.factors.contracts import canonical_bytes
    from packages.openevolve_structured.candidate import CandidateDocument
    from packages.openevolve_structured.provider_run import ProviderStructuredRunConfig
    from tests.openevolve.test_structured_persisted_identity import graph

    _, run, *_ = graph()
    value = dict(run.config)
    value["fixed_inputs"] = material["config"].fixed_inputs.model_dump(mode="json")
    search = dict(value["search"])
    search.update(
        owner_id=material["execution"]["owner_id"],
        run_id=material["execution"]["run_id"],
        execution_binding_id=material["execution"]["run_id"],
        execution_binding_sha256=digest(material["config"].fixed_inputs),
        domain=material["config"].search.domain.model_dump(mode="json"),
        objective=material["measurement"].objective.model_dump(mode="json")
        if hasattr(material["measurement"], "objective")
        else material["config"].search.objective.model_dump(mode="json"),
    )
    program = material["config"].search.domain.seed_program
    search["seed_candidate_sha256"] = digest(CandidateDocument(program=program))
    value["search"] = search
    runtime = dict(value["runtime"])
    for key in (
        "engine_image_id",
        "engine_installed_parent_image_id",
        "engine_dependency_lock_sha256",
    ):
        runtime[key] = getattr(material["config"].runtime, key)
    value["runtime"] = runtime
    return ProviderStructuredRunConfig.model_validate_json(canonical_bytes(value))


def note_graph(material):
    """Post-physical note join fixture with real result bytes and explicit row doubles.

    No successful database/SDK/controller rows are claimed or installed.
    """
    from types import SimpleNamespace
    from uuid import UUID

    from backend.app.experiment_services import dataset_identities
    from backend.app.normal_oe_note_backup import FIELDS, SCHEMA, _execution_view
    from backend.app.normal_oe_note_evidence import capture_from_scope, pin_details
    from backend.app.research_note_schemas import ResearchNoteContent
    from backend.app.research_note_services import revision_hash, search_text
    from packages.factors.contracts import canonical_bytes

    config = typed_config(material)
    execution, candidate = material["execution"], material["candidate"]
    outcome = dict(material["outcome"], id=str(uuid4()))
    owner, run_id = execution["owner_id"], execution["run_id"]
    source = material["value"].snapshot
    publication = dict(material["args"]["publication"], run_id=str(source.run_id))
    source_outcome = {
        "publication_id": publication["id"],
        "input_sha256": source.input_sha256,
        "output_sha256": source.output_rows_sha256,
    }
    version = {
        "id": str(source.input.version.factor_version_id),
        "factor_id": str(uuid4()),
        "parent_version_id": None,
        "definition": source.input.definition.model_dump(mode="json"),
        "definition_sha256": source.input.version.definition_sha256,
        "code_sha256": source.input.version.code_sha256,
    }
    facts = {
        "configs": {run_id: config},
        "measurements": {candidate["id"]: material["measurement"]},
        "epochs": {},
    }
    snapshot = {
        "execution_closure": {
            "tables": {
                "factor_execution_snapshot_publications": [publication],
                "factor_execution_snapshot_outcomes": [source_outcome],
            }
        },
        "factor_closure": {"tables": {"factor_versions": [version]}},
        "oe_structured_closure": {
            "tables": {
                "oe_structured_executions": [execution],
                "oe_structured_engine_outcomes": [outcome],
                "oe_structured_candidates": [candidate],
            }
        },
        "research_closure": {
            "external_bindings": {
                "runs": [
                    {"id": run_id, "owner_id": owner},
                    {"id": publication["run_id"], "owner_id": owner},
                ]
            }
        },
    }
    view, *_ = _execution_view(snapshot, execution, outcome, candidate, config, facts)
    dataset = {
        key: material["args"]["dataset"][key]
        for key in FIELDS["dataset_versions"].split()
    }
    assert str(source.input.dataset.dataset_version_id) == dataset["id"]
    assert source.input.dataset.content_sha256 == dataset["content_sha256"]
    snapshot["datasets"] = [
        {
            "path": dataset["storage_key"],
            "sha256": dataset["content_sha256"],
            "manifest": dataset["manifest"],
        }
    ]
    snapshot["direct_analysis_closure"] = {"tables": {"dataset_versions": [dataset]}}
    tables = {name: [] for name in FIELDS}
    tables["dataset_versions"] = [dataset]
    run = {
        "id": run_id,
        "owner_id": owner,
        "dataset_version_id": dataset["id"],
        "experiment_id": None,
        "split_version_id": None,
    }
    tables["research_runs"] = [run, dict(run, id=publication["run_id"])]
    exposure_ids = {role: str(uuid4()) for role in ("factor", "market")}
    evidence = {
        "schema_version": "mineagent.oe-structured-result-release.v1",
        "run_id": run_id,
        "execution_id": execution["id"],
        "candidate_id": candidate["id"],
        "structured_strategy_version_id": execution["program_version_id"],
        "factor_version_id": version["id"],
        "factor_id": version["factor_id"],
        "execution_snapshot_id": publication["id"],
        "snapshot_source": {
            "run_id": publication["run_id"],
            "execution_snapshot_id": publication["id"],
            "manifest_sha256": publication["manifest_sha256"],
            "input_sha256": source_outcome["input_sha256"],
            "output_sha256": source_outcome["output_sha256"],
            "extent": "conservative_full_retained_dataset",
            "qualification": "historical_disclosure_not_live_execution_authority",
        },
        "config_sha256": digest(config),
        "program_sha256": candidate["program_sha256"],
        "strategy_input_sha256": execution["strategy_input_sha256"],
        "market_sha256": execution["market_sha256"],
        "result_sha256": outcome["result_sha256"],
        "measurement_sha256": outcome["measurement_sha256"],
        "objective_sha256": outcome["objective_sha256"],
        "extent": "conservative_full_retained_dataset_including_history_and_forward_labels",
        "qualification": "fixed_snapshot_comparison_not_causal_gain",
    }
    for role, key in exposure_ids.items():
        tables["data_usage_exposures"].append(
            {
                "id": key,
                "owner_id": owner,
                "run_id": run_id,
                "experiment_id": None,
                "split_version_id": None,
                "dataset_version_id": dataset["id"],
                "source_key": f"engine-result:oe_structured_execution:{execution['id']}:{role}:v1",
                "purpose": "engine_structured_execution_release",
                "start_date": dataset["start_date"],
                "end_date": dataset["end_date"],
                "identities": dataset_identities(SimpleNamespace(**dataset)),
                "provenance": {
                    **evidence,
                    "role": role,
                    "dataset_version_id": dataset["id"],
                    "dataset_content_sha256": dataset["content_sha256"],
                },
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        )
    identity = {
        "owner_id": UUID(owner),
        "run_id": UUID(run_id),
        "execution_attempt_id": UUID(execution["attempt_id"]),
        "generation": 1,
        "execution_id": UUID(execution["id"]),
        "candidate_id": UUID(candidate["id"]),
        "program_version_id": view.structured_strategy_version_id,
        "program_sha256": view.program_sha256,
        "factor_id": view.factor_id,
        "factor_version_id": view.factor_version_id,
        "snapshot_id": view.execution_snapshot_id,
        "snapshot_input_sha256": view.snapshot_input_sha256,
        "result_sha256": view.result_sha256,
        "market_sha256": view.market_sha256,
        "config_sha256": digest(config),
        "measurement_sha256": outcome["measurement_sha256"],
    }
    scope = (identity, view, UUID(outcome["id"]), exposure_ids)
    refs = [
        capture_from_scope(scope, "normal_oe_execution"),
        capture_from_scope(
            scope, "normal_oe_decision", decision_sequence=view.decisions[0].event_seq
        ),
    ]
    if view.fills:
        refs.append(
            capture_from_scope(
                scope, "normal_oe_fill", native_fill_id=view.fills[0].native_fill_id
            )
        )
    note_id = str(uuid4())
    tables["research_notes"] = [
        {"id": note_id, "owner_id": owner, "created_at": "2026-01-01T00:00:00+00:00"}
    ]
    for revision in (1, 2):
        rid, pid = str(uuid4()), str(uuid4())
        content = ResearchNoteContent(
            title=f"Original {revision}", evidence=tuple(refs)
        )
        snapshots = [
            {
                "schema_version": "mineagent.normal-oe-note-admission.v1",
                "reference": ref.model_dump(mode="json"),
            }
            for ref in refs
        ]
        pin = {
            "id": pid,
            "revision_id": rid,
            "owner_id": owner,
            "resource_kind": "normal_oe_execution",
            "resource_id": execution["id"],
            "sha256": outcome["result_sha256"],
            "details": pin_details(refs[0]),
        }
        pin_hash = digest(
            [{k: pin[k] for k in ("resource_kind", "resource_id", "sha256", "details")}]
        )
        raw = content.model_dump(mode="json")
        row = {
            "id": rid,
            "note_id": note_id,
            "owner_id": owner,
            "revision": revision,
            "revision_sha256": revision_hash(
                note_id, revision, raw, snapshots, None, pin_hash, []
            ),
            "pins_sha256": pin_hash,
            "content": raw,
            "evidence_snapshot": snapshots,
            "admission_snapshot": [],
            "title": content.title,
            "tags": [],
            "status": content.status,
            "search_text": search_text(content),
            "change_note": None,
            "idempotency_key": str(revision),
            "request_sha256": "a" * 64,
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        tables["research_note_revisions"].append(row)
        tables["research_note_evidence_pins"].append(pin)
        tables["research_note_normal_oe_pins"].append(
            {
                "pin_id": pid,
                "owner_id": owner,
                "run_id": run_id,
                "attempt_id": execution["attempt_id"],
                "generation": 1,
                "execution_id": execution["id"],
                "engine_outcome_id": outcome["id"],
                "factor_exposure_id": exposure_ids["factor"],
                "market_exposure_id": exposure_ids["market"],
            }
        )
    for name, rows in tables.items():
        rows.sort(
            key=lambda row: row[
                "pin_id" if name == "research_note_normal_oe_pins" else "id"
            ]
        )
    snapshot["normal_oe_note_closure"] = {"schema_version": SCHEMA, "tables": tables}
    # Ensure this fixture itself contains only JSON-safe frozen values.
    canonical_bytes(snapshot)
    return snapshot, facts
