from uuid import UUID

import pytest

from packages.factors.contracts import LocalVerification, SnapshotInput, digest
from packages.factors.v2_evidence import (
    CutoffEvidence,
    EvidenceObject,
    IsolatedPrefixDeclaration,
    PrefixEvidenceV2,
    ProcessEvidence,
    SourceBinding,
    imported_v2_verification,
    validate_evidence_closure,
)
from packages.rdagent_adapter.contracts import RuntimePins
from tests.rdagent.test_rdagent_assembly import assemble, fixture


def obj(role, n, sha):
    kind, media = {
        "source": ("code", "text/plain"),
        "native_hdf": ("native_hdf", "application/x-hdf5"),
        "normalized_values": ("values_partition", "application/vnd.apache.parquet"),
        "stitched_values": ("values_partition", "application/vnd.apache.parquet"),
    }[role]
    return EvidenceObject(
        role=role,
        object_id=UUID(int=n),
        object_kind=kind,
        media_type=media,
        sha256=sha,
        size_bytes=100,
    )


def runtime():
    return RuntimePins(
        dependency_locks=tuple(
            {"role": role, "sha256": str(i) * 64}
            for i, role in enumerate(
                ("analysis", "candidate", "normalizer", "orchestrator"), 1
            )
        ),
        orchestrator_image="sha256:" + "c" * 64,
        candidate_image="sha256:" + "a" * 64,
        normalizer_image="sha256:" + "b" * 64,
        analysis_image="sha256:" + "d" * 64,
    )


def graph(**snapshot_changes):
    snapshot, prefix, ns = fixture()
    snapshot = SnapshotInput.model_validate(
        {
            **snapshot.model_dump(mode="json"),
            "materialization_profile": "mineagent.rdagent-isolated-prefix.v2",
            "adapter_version": runtime().adapter_version,
            "environment_sha256": digest(runtime()),
            **snapshot_changes,
        }
    )
    prefix = type(prefix).model_validate(
        {**prefix.model_dump(mode="json"), "snapshot_input_sha256": snapshot.sha256}
    )
    rows, assembly = assemble(snapshot, prefix, ((ns, 0, 1.5, 0),))
    binding = SourceBinding(
        owner_id=UUID(int=10),
        run_id=UUID(int=11),
        attempt_id=UUID(int=12),
        generation=1,
        generation_start_id=UUID(int=15),
        generation_source_id=UUID(int=13),
        evaluation_id=UUID(int=14),
        factor_version_id=snapshot.version.factor_version_id,
        source_sha256=snapshot.definition.code_sha256,
        snapshot_input_sha256=snapshot.sha256,
    )
    source = obj("source", 20, binding.source_sha256)
    hdf = obj("native_hdf", 21, "a" * 64)
    normalized = obj("normalized_values", 22, "b" * 64)
    final = obj("stitched_values", 23, "c" * 64)
    candidate = ProcessEvidence(
        admission_id=UUID(int=30),
        cutoff_execution_id=UUID(int=32),
        stop_receipt_sha256="d" * 64,
        extraction_receipt_sha256="e" * 64,
        image=prefix.candidate_image,
        profile=prefix.candidate_profile,
        binding=binding,
        prefix_sha256=digest(prefix),
        input_sha256=digest(prefix),
        output=hdf,
    )
    normalizer = ProcessEvidence(
        admission_id=UUID(int=31),
        cutoff_execution_id=UUID(int=32),
        stop_receipt_sha256="f" * 64,
        extraction_receipt_sha256="0" * 64,
        image=prefix.normalizer_image,
        profile=prefix.normalizer_profile,
        binding=binding,
        prefix_sha256=digest(prefix),
        input_sha256=hdf.sha256,
        output=normalized,
    )
    cutoff = CutoffEvidence(
        cutoff_execution_id=UUID(int=32),
        cutoff_ordinal=0,
        prefix=prefix,
        candidate=candidate,
        normalizer=normalizer,
        assembly=assembly,
    )
    evidence = PrefixEvidenceV2(
        schema_version="mineagent.isolated-prefix-evidence.v2",
        binding=binding,
        source=source,
        cutoffs=(cutoff,),
        stitched_values=final,
        output_rows_sha256=assembly.output_rows_sha256,
        row_count=len(rows),
        valid_count=1,
        missing_count=len(rows) - 1,
        row_assembly_sha256=digest([assembly.model_dump(mode="json")]),
        inventory=(source, hdf, normalized, final),
    )
    return snapshot, evidence


def check(evidence, snapshot, **changes):
    args = {
        "expected_binding": evidence.binding,
        "snapshot_input": snapshot,
        "expected_runtime": runtime(),
        "expected_prefixes": tuple(c.prefix for c in evidence.cutoffs),
        "observed_objects": evidence.inventory,
        "expected_output_rows_sha256": evidence.output_rows_sha256,
    }
    validate_evidence_closure(evidence, **{**args, **changes})


def test_roundtrip_and_import_always_downgrades():
    snapshot, evidence = graph()
    declaration = IsolatedPrefixDeclaration(
        schema_version="mineagent.isolated-prefix-declaration.v2",
        kind="isolated_prefix_recomputed",
        admission_profile="mineagent.isolated-prefix-recomputed.v2",
        evidence_sha256=digest(evidence),
    )
    check(
        PrefixEvidenceV2.model_validate_json(evidence.model_dump_json()),
        snapshot,
        declaration=declaration,
    )
    imported = imported_v2_verification(evidence)
    assert (
        imported.kind == "declared_or_unknown" and imported.source_pit_verified is False
    )
    with pytest.raises(ValueError):
        LocalVerification.model_validate(declaration.model_dump())


@pytest.mark.parametrize(
    "change",
    [
        "source",
        "cutoff",
        "kind",
        "hash",
        "missing",
        "extra",
        "assembly",
        "process",
        "image",
        "v1",
        "prefix_v1",
        "final_role",
    ],
)
def test_substitution_rejected(change):
    _, evidence = graph()
    raw = evidence.model_dump(mode="json")
    c = raw["cutoffs"][0]
    if change == "source":
        c["candidate"]["binding"]["source_sha256"] = "0" * 64
    elif change == "cutoff":
        c["prefix"]["host_cutoff"] = "2026-01-01T00:00:00Z"
    elif change == "kind":
        raw["inventory"][1]["object_kind"] = "native_evidence"
    elif change == "hash":
        raw["inventory"][1]["sha256"] = "0" * 64
    elif change == "missing":
        raw["inventory"].pop(1)
    elif change == "extra":
        raw["inventory"].append(raw["inventory"][0])
    elif change == "assembly":
        c["assembly"]["normalized_values_sha256"] = "0" * 64
    elif change == "process":
        c["normalizer"]["admission_id"] = c["candidate"]["admission_id"]
    elif change == "image":
        c["normalizer"]["image"] = "sha256:" + "0" * 64
    elif change == "v1":
        raw["schema_version"] = "mineagent.feature-snapshot.v1"
    elif change == "prefix_v1":
        c["prefix"]["schema_version"] = "mineagent.factor-prefix-input.v1"
    else:
        raw["stitched_values"]["role"] = "normalized_values"
    with pytest.raises(ValueError):
        PrefixEvidenceV2.model_validate(raw)


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "fixed_fixture_recomputed"),
        ("kind", "verified_source"),
        ("source_pit_verified", True),
        ("source_pit_verified", 0),
        ("source_pit_verified", "false"),
        ("authority", True),
    ],
)
def test_forged_verification_claim_rejected(field, value):
    raw = {
        "schema_version": "mineagent.isolated-prefix-declaration.v2",
        "kind": "isolated_prefix_recomputed",
        "admission_profile": "mineagent.isolated-prefix-recomputed.v2",
        "evidence_sha256": "a" * 64,
    }
    raw[field] = value
    with pytest.raises(ValueError):
        IsolatedPrefixDeclaration.model_validate(raw)


@pytest.mark.parametrize(
    "change", ["binding", "prefixes", "objects", "rows", "declaration"]
)
def test_independent_host_expectations_required(change):
    snapshot, evidence = graph()
    args = {
        "binding": {
            "expected_binding": evidence.binding.model_copy(
                update={"owner_id": UUID(int=99)}
            )
        },
        "prefixes": {"expected_prefixes": ()},
        "objects": {"observed_objects": evidence.inventory[:-1]},
        "rows": {"expected_output_rows_sha256": "0" * 64},
        "declaration": {
            "declaration": IsolatedPrefixDeclaration(
                schema_version="mineagent.isolated-prefix-declaration.v2",
                kind="isolated_prefix_recomputed",
                admission_profile="mineagent.isolated-prefix-recomputed.v2",
                evidence_sha256="0" * 64,
            )
        },
    }[change]
    with pytest.raises(ValueError):
        check(evidence, snapshot, **args)


@pytest.mark.parametrize(
    "change", ["execution", "ordinal", "generation", "receipt_missing", "oversized_hdf"]
)
def test_exact_execution_receipt_scope(change):
    _, evidence = graph()
    raw = evidence.model_dump(mode="json")
    cutoff = raw["cutoffs"][0]
    if change == "execution":
        cutoff["normalizer"]["cutoff_execution_id"] = str(UUID(int=99))
    elif change == "ordinal":
        cutoff["cutoff_ordinal"] = 1
    elif change == "generation":
        cutoff["normalizer"]["binding"]["generation"] = 2
    elif change == "receipt_missing":
        del cutoff["candidate"]["stop_receipt_sha256"]
    else:
        cutoff["candidate"]["output"]["size_bytes"] = 16_777_217
    with pytest.raises(ValueError):
        PrefixEvidenceV2.model_validate(raw)


@pytest.mark.parametrize(
    "changes",
    [
        {"materialization_profile": fixture()[0].materialization_profile},
        {"materialization_profile": "unknown.v2"},
        {"adapter_version": "mineagent.fixture.v1"},
        {"environment_sha256": "0" * 64},
    ],
)
def test_self_consistent_legacy_or_wrong_runtime_input_rejected(changes):
    snapshot, evidence = graph(**changes)
    # All source/input/prefix/process/inventory hashes have been recomputed.
    assert evidence.binding.snapshot_input_sha256 == snapshot.sha256
    with pytest.raises(ValueError, match="materialization/runtime"):
        check(evidence, snapshot)


def test_runtime_pin_substitution_rejected():
    snapshot, evidence = graph()
    pins = RuntimePins.model_validate(
        {**runtime().model_dump(mode="json"), "analysis_image": "sha256:" + "0" * 64}
    )
    with pytest.raises(ValueError, match="materialization/runtime"):
        check(evidence, snapshot, expected_runtime=pins)


def test_self_consistent_environment_cannot_hide_wrong_process_image():
    pins = RuntimePins.model_validate(
        {
            **runtime().model_dump(mode="json"),
            "candidate_image": "sha256:" + "0" * 64,
        }
    )
    snapshot, evidence = graph(environment_sha256=digest(pins))
    with pytest.raises(ValueError, match="Prefix differs"):
        check(evidence, snapshot, expected_runtime=pins)
