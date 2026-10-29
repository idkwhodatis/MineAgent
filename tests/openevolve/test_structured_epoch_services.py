"""Actual public bootstrap/intent codecs with scope and DB transaction doubles."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from backend.app import oe_structured_epoch_services as service
from backend.app.oe_structured_models import SCOPE
from packages.factors.contracts import canonical_bytes
from packages.research_runtime.process_contracts import ProcessError
from tests.openevolve.test_structured_persisted_identity import graph


def case(monkeypatch):
    original, run, *_ = graph()
    from backend.app.oe_structured_identity import decode_spec

    spec, config = decode_spec(original, run)
    config = config.model_copy(
        update={
            "runtime": config.runtime.model_copy(
                update={"controller_inventory_sha256": service.HOOK_MANIFEST_SHA256}
            )
        }
    )
    fence = NS(owner_id=run.owner_id, run_id=run.id, attempt_id=uuid4(), generation=1)
    state = NS(epoch=None, held="reserved")
    calls = []

    class Session:
        def get(self, cls, key):
            return state.epoch

        def scalar(self, statement):
            return state.epoch

        def add(self, row):
            state.epoch = row

        def flush(self):
            calls.append("flush")

    monkeypatch.setattr(service, "_current", lambda *a: (spec, config))
    monkeypatch.setattr(
        service, "resolve_operator_profile", lambda *a: (NS(), config.model_binding)
    )
    monkeypatch.setattr(
        service, "resolve_runtime", lambda *a: NS(runtime=config.runtime)
    )

    def reserve(*a, **kw):
        assert kw["purpose"] == "process_unit" and kw["amounts"].process_slots == 1
        assert (
            kw["amounts"].wall_milliseconds == 600000
            and kw["amounts"].storage_bytes == service.STORAGE_FLOOR
        )
        calls.append("reserve")
        return NS(id=uuid4(), state=state.held)

    monkeypatch.setattr(service, "reserve_budget", reserve)
    session = Session()

    def prepare():
        return service.prepare_controller_epoch(
            session, fence, NS(), process_ticket="a" * 64
        )

    return NS(
        prepare=prepare,
        state=state,
        session=session,
        fence=fence,
        calls=calls,
        config=config,
    )


def test_controller_epoch_reservation_bootstrap_and_intent_exact(monkeypatch):
    c = case(monkeypatch)
    epoch, intent, raw, seed = c.prepare()
    assert intent.admission.logical_operation_id == epoch.id
    assert epoch.controller_admission_id == intent.admission.id
    assert all(getattr(epoch, key) == getattr(c.fence, key) for key in SCOPE)
    assert c.calls == ["reserve", "flush"]  # no admission/commit/create in this helper
    _, bootstrap, same_seed = service.expected_controller_bootstrap(
        c.session, c.fence, epoch.id, NS()
    )
    assert canonical_bytes(bootstrap) == raw and same_seed == seed
    assert bootstrap.config_sha256 == service.digest(
        service.expected_config_manifest(c.config.search, seed, c.config.model_binding)
    )


def test_existing_epoch_is_not_another_launch_authority(monkeypatch):
    c = case(monkeypatch)
    c.prepare()
    with pytest.raises(ProcessError):
        c.prepare()
    assert c.calls.count("reserve") == 1


@pytest.mark.parametrize(
    "change",
    [
        "scope",
        "hash",
        "canonical",
        "malformed_json",
        "non_ascii",
        "shape",
        "binding",
        "expired_hold",
    ],
)
def test_epoch_tamper_cannot_reach_process_admission(monkeypatch, change):
    c = case(monkeypatch)
    if change == "expired_hold":
        c.state.held = "settled"
        with pytest.raises(ProcessError):
            c.prepare()
        assert c.state.epoch is None
        return
    epoch, _, _, _ = c.prepare()
    if change == "scope":
        epoch.owner_id = uuid4()
    elif change == "hash":
        epoch.bootstrap_sha256 = "0" * 64
    elif change == "canonical":
        epoch.bootstrap_canonical += "\n"
    elif change == "malformed_json":
        epoch.bootstrap_canonical = "{"
    elif change == "non_ascii":
        epoch.bootstrap_canonical = "é"
    elif change == "shape":
        epoch.bootstrap_canonical = "[]"
    elif change == "binding":
        epoch.controller_admission_id = uuid4()
    with pytest.raises(ProcessError):
        service.expected_controller_bootstrap(c.session, c.fence, epoch.id, NS())
