"""Actual canonical object/journal bytes through shared cold validator, no PG."""

import hashlib
import json
from types import SimpleNamespace as NS
from uuid import UUID, uuid4

import pytest

from backend.app import research_backup as module
from packages.factors.contracts import canonical_bytes
from packages.openevolve_structured import object_identity as codec
from packages.research_runtime.budget_contracts import BudgetVector
from tests.openevolve.test_structured_object_identity import request


def fixture(value=None, kind="oe_structured_provider_request"):
    owner, run, attempt, publication, reservation, object_id = (
        str(uuid4()) for _ in range(6)
    )
    if value is not None and hasattr(value, "owner_id"):
        owner, run, attempt = (
            str(getattr(value, field)) for field in ("owner_id", "run_id", "attempt_id")
        )
    raw = canonical_bytes(request() if value is None else value)
    sha = hashlib.sha256(raw).hexdigest()
    identity = codec.object_identity(UUID(owner), kind, sha)
    scope = {"owner_id": owner, "run_id": run, "attempt_id": attempt, "generation": 1}
    obj = {
        "id": object_id,
        "owner_id": owner,
        "object_kind": identity.object_kind,
        "sha256": sha,
        "size_bytes": len(raw),
        "storage_key": identity.storage_key,
        "media_type": identity.media_type,
    }
    pub = dict(
        id=publication,
        **scope,
        budget_reservation_id=reservation,
        object_kind=obj["object_kind"],
        sha256=sha,
        size_bytes=len(raw),
        storage_key=identity.storage_key,
        peak_bytes=2 * len(raw) + 8192,
    )
    journal = codec.publication_journal(NS(**pub))
    jkey = f"research-objects/{owner}/journals/{publication}.json"
    content = {identity.storage_key: raw, jkey: journal}
    amounts = BudgetVector(storage_bytes=pub["peak_bytes"]).model_dump(mode="json")
    indexes = {
        "research_objects": {object_id: obj},
        "research_object_publications": {publication: pub},
        "research_object_outcomes": {
            publication: {
                "object_id": object_id,
                "owner_id": owner,
                "object_kind": obj["object_kind"],
                "sha256": sha,
                "journal_sha256": hashlib.sha256(journal).hexdigest(),
            }
        },
        "research_budget_reservations": {
            reservation: dict(
                id=reservation,
                **scope,
                purpose="object_storage",
                state="settled",
                amounts=amounts,
            )
        },
        "research_budget_settlements": {
            "settlement": {
                "reservation_id": reservation,
                "outcome": "conservative_unknown",
                "charge": amounts,
            }
        },
    }
    physical = {key: {"size_bytes": len(value)} for key, value in content.items()}

    def check(key, sha, size):
        assert (
            hashlib.sha256(content[key]).hexdigest() == sha
            and len(content[key]) == size
        )

    return indexes, physical, check, lambda key: json.loads(content[key]), content


def validate(values, enabled=True):
    module._validate_objects(
        *values[:4],
        rd_source=True,
        rd_execution=True,
        rd_analysis=True,
        openevolve=True,
        rd_provider=True,
        oe_structured=enabled,
    )


def test_exact_new_object_and_publication_are_actually_revalidated():
    validate(fixture())


def test_historical_codec_does_not_accept_normal_objects():
    with pytest.raises(ValueError):
        validate(fixture(), False)


def test_successor_flag_requires_all_prior_dispatch():
    with pytest.raises(ValueError, match="successor dispatch"):
        module._validate_objects(
            {}, {}, lambda *a: None, lambda *a: None, oe_structured=True
        )


def test_actual_original_bytes_corruption_is_rejected():
    values = fixture()
    key = next(iter(values[4]))
    values[4][key] += b" "
    with pytest.raises(AssertionError):
        validate(values)


def test_pending_publication_still_parses_original_typed_body():
    values = fixture()
    idx = values[0]
    idx["research_objects"].clear()
    idx["research_object_outcomes"].clear()
    reservation = next(iter(idx["research_budget_reservations"].values()))
    reservation["state"] = "reserved"
    idx["research_budget_settlements"].clear()
    validate(values)


@pytest.mark.parametrize("pending", [False, True])
def test_actual_failure_feedback_uses_only_new_codec(pending):
    from packages.rdagent_adapter.numeric_failure import RDNumericFailureFeedback
    from tests.rdagent_provider.test_numeric_failure_contracts import frame

    value = RDNumericFailureFeedback(
        owner_id=UUID(int=1),
        run_id=UUID(int=2),
        attempt_id=UUID(int=3),
        generation=1,
        start_id=UUID(int=4),
        controller_launch_id=UUID(int=5),
        final_decision_call_id=UUID(int=6),
        failure=frame(),
        code_sha256="c" * 64,
        input_sha256="d" * 64,
        upstream_feedback=canonical_bytes(
            {
                "execution": frame().feedback_text,
                "return_checking": None,
                "code": "observed",
                "final_decision": False,
            }
        ).decode(),
    )
    values = fixture(value, "factor_generation_feedback")
    if pending:
        values[0]["research_objects"].clear()
        values[0]["research_object_outcomes"].clear()
        values[0]["research_budget_settlements"].clear()
        next(iter(values[0]["research_budget_reservations"].values()))["state"] = (
            "reserved"
        )
    validate(values)
    with pytest.raises(ValueError):
        validate(values, False)


@pytest.mark.parametrize("pending", [False, True])
def test_original_unicode_response_keeps_ascii_codec_not_legacy_utf8(pending):
    from packages.openevolve_structured.provider_contracts import OEChatResponse

    value = OEChatResponse(
        request_sha256=request().sha256,
        content="原始模型响应",
        finish_reason="stop",
        usage_source="provider",
    )
    values = fixture(value, "oe_structured_provider_response")
    if pending:
        values[0]["research_objects"].clear()
        values[0]["research_object_outcomes"].clear()
        values[0]["research_budget_settlements"].clear()
        next(iter(values[0]["research_budget_reservations"].values()))["state"] = (
            "reserved"
        )
    validate(values)


@pytest.mark.parametrize("kind", sorted(codec.KINDS))
def test_closed_new_kind_paths_require_exact_successor_schemas(kind):
    key = f"research-objects/{uuid4()}/by-kind/{kind}/{'a' * 64}.json"
    module.validate_path(key, schema=module.SCHEMA_V13)
    module.validate_path(key, schema=module.SCHEMA_V14)
    module.validate_path(key, schema=module.SCHEMA_V15)
    module.validate_path(key, schema=module.SCHEMA_V16)
    with pytest.raises(ValueError):
        module.validate_path(key, schema=module.SCHEMA_V10)
    with pytest.raises(ValueError):
        module.validate_path(key, schema="mineagent.research-restore-closure.v17")


@pytest.mark.parametrize("name", ["rejection.json", "completion.json"])
def test_new_receipt_names_are_versioned_not_success_authority(name):
    key = f"research-processes/{uuid4()}/{uuid4()}/control/{name}"
    module.validate_path(key, schema=module.SCHEMA_V13)
    module.validate_path(key, schema=module.SCHEMA_V14)
    module.validate_path(key, schema=module.SCHEMA_V15)
    module.validate_path(key, schema=module.SCHEMA_V16)
    with pytest.raises(ValueError):
        module.validate_path(key, schema=module.SCHEMA_V10)
    with pytest.raises(ValueError):
        module.validate_path(key + ".unknown", schema=module.SCHEMA_V13)
