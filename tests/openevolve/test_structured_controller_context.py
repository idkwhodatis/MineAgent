"""Real typed declarations/journals with live-peer and DB doubles; no kernel proof."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from backend.app import oe_structured_controller_context as service
from packages.factors.contracts import canonical_bytes
from packages.openevolve_structured.provider_bootstrap import (
    ProviderStructuredBootstrap,
    runtime_profile_sha256,
)
from packages.research_runtime.budget_contracts import BudgetVector
from packages.research_runtime.process_contracts import ProcessError, digest
from packages.research_runtime.process_declarations_v2 import ProcessDeclarationV2
from runtimes.openevolve_structured.profile import (
    MAX_WALL_MS,
    STORAGE_FLOOR,
    profile_sha256,
)
from runtimes.openevolve_structured_host import process_journal as journal
from tests.openevolve.test_structured_persisted_identity import graph
from tests.structured_controller_runtime.fixtures import records


def fixture(monkeypatch, tmp_path):
    session, run, _spec, _version, candidate, rows = graph()
    config = service.decode_spec(session, run)[1]
    launch, identity, child, _ = records(tmp_path)
    fence = NS(
        owner_id=run.owner_id,
        run_id=run.id,
        attempt_id=candidate.attempt_id,
        generation=1,
    )
    now = datetime.now(UTC)
    initial = ProviderStructuredBootstrap(
        admission_id=launch.intent.admission.id,
        epoch_id=candidate.epoch_id,
        spec=config.search,
        issued_at=now,
        not_after=now + timedelta(seconds=600),
        physical_profile_sha256=profile_sha256(),
        runtime_profile_sha256=runtime_profile_sha256(),
        config_sha256="a" * 64,
        adapter_patch_sha256="b" * 64,
        model_binding=config.model_binding,
        model_binding_sha256=config.model_binding.sha256,
    )
    raw = canonical_bytes(initial)
    sha = __import__("hashlib").sha256(raw).hexdigest()
    adata = launch.intent.admission.model_dump(mode="json")
    adata.update(
        {k: str(v) if k != "generation" else v for k, v in vars(fence).items()}
    )
    adata.update(
        logical_operation_id=str(candidate.epoch_id),
        image_id=config.runtime.controller_image_id,
        input_sha256=digest(
            {
                "schema_version": "mineagent.oe-structured-bootstrap-inventory.v1",
                "files": [
                    {
                        "destination": "/bootstrap/bootstrap.json",
                        "sha256": sha,
                        "size_bytes": len(raw),
                    },
                    {
                        "destination": "/bootstrap/seed.json",
                        "sha256": config.search.seed_candidate_sha256,
                        "size_bytes": len(candidate.candidate_canonical),
                    },
                ],
            }
        ),
    )
    admission = ProcessDeclarationV2.model_validate_json(canonical_bytes(adata))
    intent = journal.ControllerIntent.model_validate_json(
        canonical_bytes(
            {
                **launch.intent.model_dump(mode="json"),
                "admission": admission.model_dump(mode="json"),
                "bootstrap_sha256": sha,
                "bootstrap_size_bytes": len(raw),
                "seed_sha256": config.search.seed_candidate_sha256,
                "seed_size_bytes": len(candidate.candidate_canonical),
                "issued_at": now.isoformat(),
                "not_after": initial.not_after.isoformat(),
            }
        )
    )
    launch = journal.ControllerLaunch.model_validate_json(
        canonical_bytes(
            {**launch.model_dump(mode="json"), "intent": intent.model_dump(mode="json")}
        )
    )
    identity = journal.ControllerIdentity.model_validate_json(
        canonical_bytes(
            {
                **identity.model_dump(mode="json"),
                "launch_sha256": digest(launch.model_dump(mode="json")),
                "admission_sha256": digest(admission.model_dump(mode="json")),
                "image_id": admission.image_id,
            }
        )
    )
    child = journal.ControllerChild.model_validate_json(
        canonical_bytes(
            {
                **child.model_dump(mode="json"),
                "identity_sha256": digest(identity.model_dump(mode="json")),
                "admission_sha256": identity.admission_sha256,
            }
        )
    )
    journal.validate_records(launch, identity, child)

    def row(**kw):
        return NS(**vars(fence), **kw)

    epoch = row(
        id=candidate.epoch_id,
        controller_admission_id=admission.id,
        bootstrap_canonical=raw.decode(),
        bootstrap_sha256=sha,
    )
    adm = NS(
        **admission.model_dump(mode="python"),
        ipc_policy="host_ticket",
        ticket_sha256=admission.ipc.ticket_sha256,
    )
    state = row(admission_id=admission.id, status="running")
    reservation = row(
        id=admission.reservation_id,
        state="reserved",
        purpose="process_unit",
        logical_key="oe-structured-controller:" + str(epoch.id),
        amounts=BudgetVector(
            process_slots=1, wall_milliseconds=MAX_WALL_MS, storage_bytes=STORAGE_FLOOR
        ).model_dump(),
    )
    receipts = [
        row(
            kind=kind,
            payload=value.model_dump(mode="json"),
            payload_sha256=digest(value.model_dump(mode="json")),
        )
        for kind, value in (
            ("launch_intent", intent),
            ("launch_identity", identity),
            ("controller_child", child),
        )
    ]
    bytype = {
        service.OEStructuredEpoch: epoch,
        service.ResearchProcessAdmission: adm,
        service.ResearchProcessState: state,
        service.ResearchBudgetReservation: reservation,
    }

    class Session:
        def get(self, cls, key):
            return bytype[cls] if cls in bytype else rows.get((cls, key))

        def scalars(self, stmt):
            return NS(
                all=lambda: (
                    ["028_oe_structured_search"]
                    if str(stmt) == "SELECT version_num FROM alembic_version"
                    else receipts
                )
            )

        def scalar(self, stmt):
            return None

    peer = NS(
        admission=admission,
        binding=service.ControllerBinding(
            identity.container_id,
            identity.admission_sha256,
            identity.leader,
            child.controller,
        ),
    )
    calls = []
    monkeypatch.setattr(service, "Peer", NS)
    monkeypatch.setattr(service, "require_peer", lambda *a, **kw: calls.append(kw))
    monkeypatch.setattr(
        service, "require_neutral_transaction", lambda *a, **kw: (run, None)
    )
    monkeypatch.setattr(service, "require_native_storage_guard", lambda root: None)
    monkeypatch.setattr(
        service,
        "read_controller_journal",
        lambda *a, **kw: (launch, identity, child, None),
    )
    return NS(
        session=Session(),
        fence=fence,
        epoch=epoch,
        peer=peer,
        calls=calls,
        receipts=receipts,
        state=state,
        reservation=reservation,
        settings=NS(storage_root=tmp_path),
    )


def read(c):
    return service.require_controller_context(
        c.session, c.fence, c.epoch.id, c.settings, peer=c.peer
    )


def test_real_reserved_enum_and_pre_post_live_checks(monkeypatch, tmp_path):
    c = fixture(monkeypatch, tmp_path)
    assert read(c).epoch.id == c.epoch.id
    assert len(c.calls) == 2


@pytest.mark.parametrize("state", ["uncertain", "settled", "held"])
def test_non_reserved_controller_cannot_grant_work(monkeypatch, tmp_path, state):
    c = fixture(monkeypatch, tmp_path)
    c.reservation.state = state
    with pytest.raises(ProcessError):
        read(c)


@pytest.mark.parametrize(
    "field,value",
    [("process_slots", 0), ("wall_milliseconds", 1), ("storage_bytes", 1)],
)
def test_controller_peak_floor_not_just_process_status(
    monkeypatch, tmp_path, field, value
):
    c = fixture(monkeypatch, tmp_path)
    c.reservation.amounts[field] = value
    with pytest.raises(ProcessError):
        read(c)


def test_missing_child_receipt_not_inferred_from_live_peer(monkeypatch, tmp_path):
    c = fixture(monkeypatch, tmp_path)
    c.receipts.pop()
    with pytest.raises(ProcessError):
        read(c)


def test_tampered_db_identity_not_replaced_by_retained_physical_journal(
    monkeypatch, tmp_path
):
    c = fixture(monkeypatch, tmp_path)
    c.receipts[1].payload_sha256 = "0" * 64
    with pytest.raises(ProcessError):
        read(c)
