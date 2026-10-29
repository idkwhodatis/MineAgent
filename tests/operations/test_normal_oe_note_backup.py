# ruff: noqa: F811
"""032 separate notes capability cannot silently alter old research formats."""

from dataclasses import replace

import pytest

from backend.app.normal_oe_note_backup import (
    FIELDS,
    SCHEMA,
    closure_for,
    validate_snapshot,
)
from backend.app.schema_capabilities import capabilities_for


def empty():
    return {
        "normal_oe_note_closure": {
            "schema_version": SCHEMA,
            "tables": {key: [] for key in FIELDS},
        }
    }


def test_032_only_adds_independent_notes_capability():
    old = capabilities_for("031_workflow_structured_stage")
    new = capabilities_for("032_normal_oe_note_pins")
    assert replace(new, normal_oe_note_schema=None) == old
    assert new.normal_oe_note_schema == SCHEMA


def test_old_revision_rejects_new_closure():
    with pytest.raises(ValueError):
        closure_for(empty(), capabilities_for("031_workflow_structured_stage"), 100)


def test_new_revision_requires_new_closure():
    with pytest.raises(ValueError):
        closure_for({}, capabilities_for("032_normal_oe_note_pins"), 100)


@pytest.mark.parametrize(
    "field",
    [
        "research_closure_schema",
        "rd_source_closure_schema",
        "oe_structured_closure_schema",
        "workflow_schema",
    ],
)
def test_mixed_parent_formats_rejected(field):
    caps = replace(capabilities_for("032_normal_oe_note_pins"), **{field: "unknown"})
    with pytest.raises(ValueError):
        closure_for(empty(), caps, 100)


def test_empty_metadata_is_not_physical_verification():
    with pytest.raises(ValueError):
        validate_snapshot(
            empty(), capabilities_for("032_normal_oe_note_pins"), 100, oe_facts=None
        )


@pytest.mark.parametrize(
    "change",
    [
        "unknown_table",
        "missing_table",
        "wrong_schema",
        "duplicate_row",
        "unsorted_rows",
    ],
)
def test_closed_inventory_shape(change):
    from uuid import UUID

    snapshot = empty()
    closure = snapshot["normal_oe_note_closure"]
    if change == "unknown_table":
        closure["tables"]["unknown"] = []
    elif change == "missing_table":
        closure["tables"].pop("research_notes")
    elif change == "wrong_schema":
        closure["schema_version"] = "mineagent.note-normal-oe-closure.v2"
    else:
        row = {"id": str(UUID(int=1)), "symbol": "A"}
        other = dict(row, id=str(UUID(int=2)))
        closure["tables"]["listings"] = (
            [row, row] if change == "duplicate_row" else [other, row]
        )
    with pytest.raises(ValueError):
        closure_for(snapshot, capabilities_for("032_normal_oe_note_pins"), 100)


def test_read_cursor_is_statement_streaming_and_closed():
    from backend.app.normal_oe_note_backup import read_closure

    class Result:
        closed = False

        def scalars(self):
            return iter(())

        def close(self):
            self.closed = True

    class Connection:
        def __init__(self):
            self.results = []

        def execute(self, statement, parameters):
            assert statement.get_execution_options() == {
                "stream_results": True,
                "yield_per": 1,
            }
            assert parameters == {"limit": 101}
            result = Result()
            self.results.append(result)
            return result

    connection = Connection()
    assert read_closure(connection, 100) == empty()["normal_oe_note_closure"]
    assert len(connection.results) == len(FIELDS)
    assert all(result.closed for result in connection.results)


@pytest.mark.parametrize(
    "change", [None, "result", "measurement", "definition", "input"]
)
def test_actual_engine_bytes_rebuild_note_view(retained_declaration, tmp_path, change):
    """Real engine-byte reader first; not a complete controller/PG closure proof."""
    from uuid import uuid4

    from backend.app.normal_oe_note_backup import _execution_view
    from tests.operations.normal_oe_note_fixture import actual_engine_material

    material = actual_engine_material(retained_declaration, tmp_path)
    value = material["value"]
    source_snapshot = value.snapshot
    publication = dict(
        material["args"]["publication"], run_id=str(source_snapshot.run_id)
    )
    source = {
        "publication_id": publication["id"],
        "input_sha256": source_snapshot.input_sha256,
    }
    version = {
        "id": str(source_snapshot.input.version.factor_version_id),
        "factor_id": str(uuid4()),
        "parent_version_id": None,
        "definition": source_snapshot.input.definition.model_dump(mode="json"),
        "definition_sha256": source_snapshot.input.version.definition_sha256,
        "code_sha256": source_snapshot.input.version.code_sha256,
    }
    snapshot = {
        "execution_closure": {
            "tables": {
                "factor_execution_snapshot_publications": [publication],
                "factor_execution_snapshot_outcomes": [source],
            }
        },
        "factor_closure": {"tables": {"factor_versions": [version]}},
    }
    if change == "result":
        material["outcome"]["result_sha256"] = "0" * 64
    elif change == "measurement":
        material["outcome"]["measurement_sha256"] = "0" * 64
    elif change == "definition":
        version["definition_sha256"] = "0" * 64
    elif change == "input":
        source["input_sha256"] = "0" * 64
    args = (
        snapshot,
        material["execution"],
        material["outcome"],
        material["candidate"],
        material["config"],
        {"measurements": {material["candidate"]["id"]: material["measurement"]}},
    )
    if change:
        with pytest.raises(ValueError):
            _execution_view(*args)
    else:
        view, *_ = _execution_view(*args)
        assert view.result_sha256 == material["outcome"]["result_sha256"]
        assert view.decisions == material["result"].decisions


from tests.structured.test_reconstruction import retained_declaration  # noqa: F401


@pytest.mark.parametrize(
    "change",
    [
        None,
        "missing_root",
        "duplicate_root",
        "wrong_owner",
        "wrong_path",
        "same_hash_wrong_id",
        "changed_manifest",
        "direct_disagreement",
    ],
)
def test_two_distinct_dataset_bindings_require_exact_root_inventory(
    retained_declaration, change
):
    """Real retained manifest bytes, metadata joining only; no fake physical gate."""
    import copy
    from uuid import uuid4

    from backend.app.normal_oe_note_backup import _dataset_inventory

    original = retained_declaration[0]["dataset"]
    first = {key: original[key] for key in FIELDS["dataset_versions"].split()}
    second = copy.deepcopy(first)
    second["id"] = str(uuid4())
    second["storage_key"] = f"datasets/{second['owner_id']}/{second['id']}"
    # Same original bytes may have distinct registrations; identity is never hash alone.
    indexed = {"dataset_versions": {r["id"]: r for r in (first, second)}}
    root = [
        {
            "path": r["storage_key"],
            "sha256": r["content_sha256"],
            "manifest": copy.deepcopy(r["manifest"]),
        }
        for r in (first, second)
    ]
    snapshot = {
        "datasets": root,
        "direct_analysis_closure": {
            "tables": {"dataset_versions": [copy.deepcopy(first)]}
        },
    }
    if change == "missing_root":
        root.pop()
    elif change == "duplicate_root":
        root.append(copy.deepcopy(root[1]))
    elif change == "wrong_owner":
        second["owner_id"] = str(uuid4())
    elif change == "wrong_path":
        second["storage_key"] += "/other"
    elif change == "same_hash_wrong_id":
        second["id"] = str(uuid4())
    elif change == "changed_manifest":
        root[1]["manifest"]["tampered"] = True
    elif change == "direct_disagreement":
        snapshot["direct_analysis_closure"]["tables"]["dataset_versions"][0][
            "content_sha256"
        ] = "0" * 64
    if change:
        with pytest.raises(ValueError):
            _dataset_inventory(snapshot, indexed)
    else:
        _dataset_inventory(snapshot, indexed)


@pytest.mark.parametrize(
    "change",
    [
        None,
        "revision_bytes",
        "pin_hash",
        "orphan_attachment",
        "missing_attachment",
        "duplicate_pin",
        "wrong_owner",
    ],
)
def test_all_note_revisions_and_mixed_pin_inventory_are_bound(change):
    """Actual note codec/hash, explicit DTO doubles; not physical acceptance."""
    from uuid import uuid4

    from backend.app.normal_oe_note_backup import _revisions
    from backend.app.normal_oe_note_evidence import capture_from_scope, pin_details
    from backend.app.research_note_schemas import ResearchNoteContent
    from backend.app.research_note_services import revision_hash, search_text
    from backend.app.services import DomainError
    from packages.factors.contracts import digest
    from tests.openevolve.test_normal_note_evidence import scope

    actual = scope()
    ref = capture_from_scope(actual, "normal_oe_execution")
    owner, note_id = str(ref.owner_id), str(uuid4())
    indexed = {key: {} for key in FIELDS}
    indexed["research_notes"][note_id] = {"id": note_id, "owner_id": owner}
    for ordinal in (1, 2):
        revision_id, pin_id, artifact_id = (str(uuid4()) for _ in range(3))
        content = ResearchNoteContent(title=f"Revision {ordinal}", evidence=(ref,))
        pins = [
            {
                "id": pin_id,
                "revision_id": revision_id,
                "owner_id": owner,
                "resource_kind": "normal_oe_execution",
                "resource_id": str(ref.execution_id),
                "sha256": ref.result_sha256,
                "details": pin_details(ref),
            },
            {
                "id": artifact_id,
                "revision_id": revision_id,
                "owner_id": owner,
                "resource_kind": "artifact",
                "resource_id": str(uuid4()),
                "sha256": "a" * 64,
                "details": {"storage_key": "legacy/preserved", "size_bytes": 1},
            },
        ]
        inventory = [
            {k: p[k] for k in ("resource_kind", "resource_id", "sha256", "details")}
            for p in sorted(pins, key=lambda p: (p["resource_kind"], p["resource_id"]))
        ]
        snapshots = [
            {
                "schema_version": "mineagent.normal-oe-note-admission.v1",
                "reference": ref.model_dump(mode="json"),
            }
        ]
        value = content.model_dump(mode="json")
        row = {
            "id": revision_id,
            "note_id": note_id,
            "owner_id": owner,
            "revision": ordinal,
            "content": value,
            "evidence_snapshot": snapshots,
            "admission_snapshot": [],
            "change_note": None,
            "pins_sha256": digest(inventory),
            "title": content.title,
            "tags": list(content.tags),
            "status": content.status,
            "search_text": search_text(content),
        }
        row["revision_sha256"] = revision_hash(
            note_id, ordinal, value, snapshots, None, row["pins_sha256"], []
        )
        indexed["research_note_revisions"][revision_id] = row
        indexed["research_note_evidence_pins"].update({p["id"]: p for p in pins})
        indexed["research_note_normal_oe_pins"][pin_id] = {"pin_id": pin_id}
    first = next(iter(indexed["research_note_revisions"].values()))
    pin = next(iter(indexed["research_note_evidence_pins"].values()))
    if change == "revision_bytes":
        first["content"]["body"] = "changed historical revision"
    elif change == "pin_hash":
        pin["sha256"] = "0" * 64
    elif change == "orphan_attachment":
        indexed["research_note_normal_oe_pins"][str(uuid4())] = {}
    elif change == "missing_attachment":
        indexed["research_note_normal_oe_pins"].pop(pin["id"])
    elif change == "duplicate_pin":
        indexed["research_note_evidence_pins"][str(uuid4())] = dict(pin)
    elif change == "wrong_owner":
        pin["owner_id"] = str(uuid4())
    if change:
        with pytest.raises((ValueError, DomainError)):
            _revisions(indexed)
    else:
        contents, normal = _revisions(indexed)
        assert len(contents) == len(normal) == 2
        assert len(indexed["research_note_evidence_pins"]) == 4


def test_actual_bytes_complete_typed_config_fixture(retained_declaration, tmp_path):
    from tests.operations.normal_oe_note_fixture import (
        actual_engine_material,
        typed_config,
    )

    material = actual_engine_material(retained_declaration, tmp_path)
    config = typed_config(material)
    assert str(config.search.run_id) == material["execution"]["run_id"]
    assert config.fixed_inputs.snapshot_id == material["value"].snapshot.snapshot_id


@pytest.mark.parametrize(
    "change",
    [
        None,
        "exposure_window",
        "exposure_purpose",
        "exposure_role",
        "exposure_identity",
        "generation_bool",
        "outcome_hash",
        "owner",
        "extra_exposure",
        "decision_rowhash",
        "source_dataset_swap",
        "normal_dataset_swap",
    ],
)
def test_nonempty_postphysical_notes_join(retained_declaration, tmp_path, change):
    """Actual result/codec bytes plus explicit DB/facts doubles, not full cold restore."""
    from uuid import uuid4

    from tests.operations.normal_oe_note_fixture import (
        actual_engine_material,
        note_graph,
    )

    snapshot, facts = note_graph(actual_engine_material(retained_declaration, tmp_path))
    tables = snapshot["normal_oe_note_closure"]["tables"]
    exposure = tables["data_usage_exposures"][0]
    assert {
        r["kind"] for r in tables["research_note_revisions"][0]["content"]["evidence"]
    } == {"normal_oe_execution", "normal_oe_decision"}
    if change in {"decision_rowhash", "fill_rowhash", "fill_listing"}:
        from backend.app.research_note_services import revision_hash

        revision = tables["research_note_revisions"][0]
        kind = (
            "normal_oe_decision" if change == "decision_rowhash" else "normal_oe_fill"
        )
        ordinal = next(
            i
            for i, r in enumerate(revision["content"]["evidence"])
            if r["kind"] == kind
        )
        field = "listing_id" if change == "fill_listing" else "row_sha256"
        value = str(uuid4()) if field == "listing_id" else "0" * 64
        revision["content"]["evidence"][ordinal][field] = value
        revision["evidence_snapshot"][ordinal]["reference"][field] = value
        revision["revision_sha256"] = revision_hash(
            revision["note_id"],
            revision["revision"],
            revision["content"],
            revision["evidence_snapshot"],
            revision["change_note"],
            revision["pins_sha256"],
            revision["admission_snapshot"],
        )
    if change in {"source_dataset_swap", "normal_dataset_swap"}:
        import copy

        own_id = tables["research_note_normal_oe_pins"][0]["run_id"]
        source_id = snapshot["execution_closure"]["tables"][
            "factor_execution_snapshot_publications"
        ][0]["run_id"]
        selected_id = source_id if change == "source_dataset_swap" else own_id
        selected = next(r for r in tables["research_runs"] if r["id"] == selected_id)
        replacement = copy.deepcopy(tables["dataset_versions"][0])
        replacement["id"] = str(uuid4())
        replacement["storage_key"] = (
            f"datasets/{replacement['owner_id']}/{replacement['id']}"
        )
        tables["dataset_versions"].append(replacement)
        tables["dataset_versions"].sort(key=lambda r: r["id"])
        snapshot["datasets"].append(
            {
                "path": replacement["storage_key"],
                "sha256": replacement["content_sha256"],
                "manifest": copy.deepcopy(replacement["manifest"]),
            }
        )
        selected["dataset_version_id"] = replacement["id"]
        if change == "source_dataset_swap":
            factor = next(
                r
                for r in tables["data_usage_exposures"]
                if r["provenance"]["role"] == "factor"
            )
            factor["dataset_version_id"] = replacement["id"]
            factor["provenance"]["dataset_version_id"] = replacement["id"]
    if change == "exposure_window":
        exposure["end_date"] = "2099-01-01"
    elif change == "exposure_purpose":
        exposure["purpose"] = "result_preview"
    elif change == "exposure_role":
        exposure["provenance"]["role"] = "unknown"
    elif change == "exposure_identity":
        exposure["identities"] = []
    elif change == "generation_bool":
        tables["research_note_normal_oe_pins"][0]["generation"] = True
    elif change == "outcome_hash":
        snapshot["oe_structured_closure"]["tables"]["oe_structured_engine_outcomes"][0][
            "result_sha256"
        ] = "0" * 64
    elif change == "owner":
        tables["research_note_normal_oe_pins"][0]["owner_id"] = str(uuid4())
    elif change == "extra_exposure":
        tables["data_usage_exposures"].append(dict(exposure, id=str(uuid4())))
        tables["data_usage_exposures"].sort(key=lambda r: r["id"])
    if change:
        with pytest.raises(ValueError):
            validate_snapshot(
                snapshot,
                capabilities_for("032_normal_oe_note_pins"),
                1000,
                oe_facts=facts,
            )
    else:
        validate_snapshot(
            snapshot, capabilities_for("032_normal_oe_note_pins"), 1000, oe_facts=facts
        )
        assert len(tables["research_note_revisions"]) == 2


@pytest.mark.parametrize(
    "change", [None, "hash", "listing", "date", "missing", "duplicate"]
)
def test_fill_row_exact_projection_boundary(change):
    """Explicit view double: complements real no-fill output, not fabricated fills."""
    from uuid import uuid4

    from backend.app.normal_oe_note_backup import validate_row_reference
    from backend.app.normal_oe_note_evidence import capture_from_scope
    from tests.openevolve.test_normal_note_evidence import scope

    actual = scope()
    reference = capture_from_scope(
        actual, "normal_oe_fill", native_fill_id="normal/actual-fill"
    )
    if change == "hash":
        reference = reference.model_copy(update={"row_sha256": "0" * 64})
    elif change == "listing":
        reference = reference.model_copy(update={"listing_id": uuid4()})
    elif change == "date":
        reference = reference.model_copy(update={"session_date": "2025-01-01"})
    elif change == "missing":
        actual[1].fills.clear()
    elif change == "duplicate":
        actual[1].fills.append(actual[1].fills[0])
    if change:
        with pytest.raises(ValueError):
            validate_row_reference(reference, actual[1])
    else:
        validate_row_reference(reference, actual[1])
