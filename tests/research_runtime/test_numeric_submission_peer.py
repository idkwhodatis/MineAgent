"""Host callback ordering and fail-closed doubles, not Docker/PG acceptance."""

from types import SimpleNamespace

import pytest

from packages.research_runtime.process_contracts import ProcessError
from runtimes.research_supervisor.numeric_supervisor import (
    NumericSupervisor,
    _mint_numeric_authority,
)
from runtimes.research_supervisor.supervisor import operation_path, process_fence
from tests.research_runtime.test_numeric_issuer import bound
from workers.research.numeric_process import _authorize_submission


@pytest.mark.parametrize(
    "callback", [False, {}, lambda *_a: None, lambda *_a: {"approved": True}]
)
def test_callback_is_not_a_noop_or_serialized_grant(callback):
    with pytest.raises(ProcessError, match="numeric_controller"):
        _authorize_submission(object(), object(), callback)


def test_explicit_component_fixture_can_omit_controller():
    _authorize_submission(object(), object(), None)


def test_forged_exact_peer_type_still_requires_live_issuer():
    from runtimes.rdagent_operational.peer import ControllerPeer

    peer = object.__new__(ControllerPeer)
    object.__setattr__(peer, "_issuer", object())
    object.__setattr__(
        peer, "admission", SimpleNamespace(logical_operation_id=object())
    )
    with pytest.raises(ProcessError, match="rd_controller_peer"):
        _authorize_submission(object(), object(), lambda *_a: peer)


@pytest.mark.parametrize("deny_at", [1, 2])
def test_peer_loss_after_staging_or_create_prevents_start_without_fake_stop(
    tmp_path, monkeypatch, deny_at
):
    intent, inputs = bound()
    supervisor = NumericSupervisor(intent.admission.image_id)
    authority = _mint_numeric_authority(
        intent, inputs, {"image_id": intent.admission.image_id}
    )
    commands, checks = [], []
    cid = "c" * 64
    state = {
        "Id": cid,
        "State": {
            "Running": False,
            "Status": "created",
            "StartedAt": "0001-01-01T00:00:00Z",
        },
    }

    class Client:
        deadline = None
        present = False

        def _command(self, args):
            commands.append(args[0])
            if args[0] == "create":
                self.present = True
                return 0, cid.encode()
            assert args == ["rm", "--force", cid]
            self.present = False
            return 0, b""

        def _inspect(self, _name):
            return state if self.present else None

    supervisor.client = Client()
    monkeypatch.setattr(supervisor, "_verify", lambda *_a: state)
    monkeypatch.setattr(supervisor, "verified_inventory", lambda *_a: {})
    monkeypatch.setattr(
        "runtimes.research_supervisor.numeric_supervisor.AttachStream.connect",
        lambda *_a, **_kw: SimpleNamespace(close=lambda: None),
    )

    def authorize():
        # Acquiring this exact flock proves DB/peer callbacks do not run while
        # the supervisor holds its namespace fence. No Docker calls are real.
        with process_fence(tmp_path):
            checks.append(len(commands))
        if len(checks) == deny_at:
            raise ProcessError("synthetic_controller_lost")

    with pytest.raises(ProcessError):
        supervisor.execute(
            tmp_path,
            authority,
            on_identity=lambda _i: pytest.fail("must not start"),
            before_submission=authorize,
        )
    assert len(checks) == deny_at
    assert commands == ([] if deny_at == 1 else ["create", "rm"])
    path = operation_path(tmp_path, intent.admission)
    assert (path / "control" / "launch.json").is_file()
    assert not (path / "control" / "identity.json").exists()
    assert not (path / "control" / "stop.json").exists()
    assert not (path / "control" / "extraction.json").exists()
    assert len(list((path / "inputs").iterdir())) == len(inputs)
