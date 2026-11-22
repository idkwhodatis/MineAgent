"""Ensure optional native projection remains exact and private."""

from pathlib import Path

from script.workflow_heartbeat_production_report import (
    BRIDGE,
    CASES,
    RESTORE,
    _anchored_phase,
    _bridge_phase,
    _drain_observations,
    _drain_verdict,
    _release_details,
    coordination,
    outcome,
)


def test_exact_fixture_source_anchors():
    fixture = Path("tests/integration/workflow_structured_stage_fixture.py").read_text()
    bridge = fixture.split("def run_bridge(", 1)[1].split("def assert_restore(", 1)[0]
    occurrences = {"drain(env.factory, env.settings)": 2}
    for anchor, expected_phase in BRIDGE.items():
        indices = [
            i
            for i, value in enumerate(bridge.splitlines(), 1)
            if value.strip() == anchor
        ]
        assert (
            _anchored_phase(
                bridge, indices[-1], BRIDGE, "bridge", occurrences=occurrences
            )
            == expected_phase
        )
    restore = Path(
        "tests/integration/test_workflow_structured_stage_flow.py"
    ).read_text()
    for anchor, expected_phase in RESTORE.items():
        index = next(
            i
            for i, value in enumerate(restore.splitlines(), 1)
            if value.strip() == anchor
        )
        assert (
            _anchored_phase(restore, index, RESTORE, "before_bridge") == expected_phase
        )


def _write(root, test_case, contents):
    module, test = CASES[test_case]
    path = root / f"{test_case}.xml"
    path.write_text(
        '<testsuite><testcase classname="tests.integration.'
        + module
        + '" name="'
        + test
        + '">'
        + contents
        + "</testcase></testsuite>"
    )
    return path


def test_closed_projection_will_not_return_raw_test_output(tmp_path):
    _write(
        tmp_path,
        "bridge",
        "<failure>secret-canary</failure><properties>"
        '<property name="proof_phase" value="rd_drain"/>'
        '<property name="proof_code" value="secret-canary"/>'
        "</properties>",
    )
    assert outcome(tmp_path, "bridge") == ("failure", "rd_drain", "unavailable")


def test_projection_fails_closed_on_mismatched_inventory_and_skip(tmp_path):
    path = _write(tmp_path, "heartbeat", "<skipped>private</skipped>")
    assert outcome(tmp_path, "heartbeat") == ("skipped", "unavailable", "unavailable")
    path.write_text(path.read_text().replace(CASES["heartbeat"][1], "different"))
    assert outcome(tmp_path, "heartbeat") == (
        "unavailable",
        "unavailable",
        "unavailable",
    )


def test_projection_only_accepts_exact_pass(tmp_path):
    _write(tmp_path, "restore", "")
    assert outcome(tmp_path, "restore") == ("passed", "complete", "unavailable")


def test_coordination_projection_requires_exact_distinct_cases(tmp_path):
    classname = "tests.integration.test_workflow_heartbeat_rpc_coordination"
    expected = {
        "heartbeat-commits-first": "heartbeat_commits_first",
        "rpc-commits-first": "rpc_commits_first",
        "rpc-rolls-back-first": "rpc_rolls_back_first",
    }
    path = tmp_path / "coordination.xml"

    def write(parameters):
        records = "".join(
            f'<testcase classname="{classname}" '
            f'name="test_exact_workflow_rpc_coordinates_with_heartbeat[{param}]">'
            f"{detail}</testcase>"
            for param, detail in parameters
        )
        path.write_text(f"<testsuite>{records}</testsuite>")

    write([(key, "") for key in expected])
    assert coordination(tmp_path) == dict.fromkeys(expected.values(), "passed")
    write(
        [
            ("heartbeat-commits-first", ""),
            ("rpc-commits-first", ""),
            ("rpc-rolls-back-first", "<skipped>private-canary</skipped>"),
        ]
    )
    assert coordination(tmp_path) == {
        "heartbeat_commits_first": "passed",
        "rpc_commits_first": "passed",
        "rpc_rolls_back_first": "skipped",
    }
    write([("heartbeat-commits-first", "")] * 3)
    assert coordination(tmp_path) == dict.fromkeys(expected.values(), "unavailable")
    path.unlink()
    path.symlink_to("not-a-real-report.xml")
    assert coordination(tmp_path) == dict.fromkeys(expected.values(), "unavailable")


def test_waiting_release_checkpoint_and_worker_drain_do_not_collapse():
    fixture = Path("tests/integration/workflow_structured_stage_fixture.py").read_text()
    source = fixture.split("def run_bridge(", 1)[1].split("def assert_restore(", 1)[0]
    lines = source.splitlines()
    release = next(
        i for i, line in enumerate(lines, 1) if line.strip() == '== "waiting_release"'
    )
    assert _bridge_phase(source, release - 3) == "rd_drain"
    assert _bridge_phase(source, release - 2) == "rd_waiting_release"
    assert _bridge_phase(source, release + 4) == "rd_checkpoint_readback"


def test_oe_claim_call_is_distinct_from_release_assertion():
    source = Path("tests/integration/workflow_structured_stage_fixture.py").read_text()
    source = source.split("def run_bridge(", 1)[1].split("def assert_restore(", 1)[0]
    lines = [line.strip() for line in source.splitlines()]
    assignment = (
        lines.index(
            "oe_claim = drive_one(env.factory, env.settings, owner_id=owner, workflow_id=wid)"
        )
        + 1
    )
    assert _bridge_phase(source, assignment - 1) == "rd_release_owner"
    assert _bridge_phase(source, assignment) == "oe_claim"
    assert _bridge_phase(source, assignment + 1) == "oe_claim"
    comparison = lines.index('== "child_owned_by_engine_worker"') + 1
    assert _bridge_phase(source, comparison - 2) == "oe_queue_boundary"


def test_only_exact_release_assertion_can_report_closed_http_status(tmp_path):
    from httpx import Response

    values = {
        "path": "analyses/" + "x" * 36,
        "released": Response(409, content=b"private-canary"),
    }
    assert _release_details(
        "assert released.status_code == 200, released.text", values
    ) == {"release_resource": "analysis", "release_status": "http_409"}
    assert _release_details(
        'released = env.client.post(route + "/release", headers=env.headers)', values
    ) == {"release_resource": "analysis"}
    _write(
        tmp_path,
        "bridge",
        '<properties><property name="proof_phase" value="rd_release_owner"/>'
        '<property name="rd_drain_release_resource" value="analysis"/>'
        '<property name="rd_drain_release_status" value="http_409"/></properties>'
        "<failure>private</failure>",
    )
    assert _drain_verdict(tmp_path, "bridge") == {
        "proof_phase": "rd_release_owner",
        "release_resource": "analysis",
        "release_status": "http_409",
    }


def test_only_closed_first_error_and_exact_known_roles_escape_private_stdout():
    captured = (
        'RD_QUEUE_FAILURE:{"family":"database","sqlstate":"55P03","stage":"queue","code":"private-test-key","untrusted":"secret"}\n'
        'RD_QUEUE_FAILURE:{"family":"domain","sqlstate":"none","stage":"controller","code":"deadline"}\n'
        'RD_QUEUE_STATE:{"status":"failed","unit_overflow":false,"units":[{"role":"rd_orchestrator","state":"stopped"},{"role":"native_evaluator","state":"secret-fixture"},{"role":"factor_normalizer","state":"running"},{"role":"factor_normalizer","state":"stopped"}]}\n'
    )
    assert _drain_observations(captured) == {
        "family": "database",
        "sqlstate": "55P03",
        "stage": "queue",
        "run": "failed",
        "rd_orchestrator": "stopped",
    }
    assert _drain_observations(captured + "RD_QUEUE_STATE:{invalid}") == {}


def test_projection_revalidates_properties_and_does_not_publish_passes(tmp_path):
    properties = (
        '<properties><property name="proof_phase" value="rd_waiting_release"/>'
        '<property name="rd_drain_code" value="deadline"/>'
        '<property name="rd_drain_family" value="secret-canary"/>'
        '<property name="rd_drain_run" value="failed"/>'
        '<property name="rd_drain_run" value="running"/></properties>'
    )
    _write(tmp_path, "bridge", properties + "<failure>private</failure>")
    assert _drain_verdict(tmp_path, "bridge") == {
        "proof_phase": "rd_waiting_release",
        "code": "deadline",
    }
    _write(tmp_path, "bridge", properties)
    assert _drain_verdict(tmp_path, "bridge") == {}


def test_observer_cut_projects_only_the_first_closed_observer_stage(tmp_path):
    captured = (
        'RD_QUEUE_FAILURE:{"family":"process","code":"rd_controller_daemon_identity",'
        '"stage":"observer","observer_failure":"container_state","rpc_method":"check_control"}\n'
        'RD_QUEUE_FAILURE:{"stage":"observer","observer_failure":"deadline"}\n'
    )
    assert _drain_observations(captured) == {
        "family": "process",
        "code": "rd_controller_daemon_identity",
        "stage": "observer",
        "observer_failure": "container_state",
        "rpc_method": "check_control",
    }
    assert "observer_failure" not in _drain_observations(
        captured.replace("container_state", "private-canary", 1)
    )
    assert "observer_failure" not in _drain_observations(
        captured.replace('"observer"', '"controller"', 1)
    )
    properties = (
        '<properties><property name="proof_phase" value="rd_waiting_release"/>'
        '<property name="rd_drain_stage" value="observer"/>'
        '<property name="rd_drain_observer_failure" value="deadline"/></properties>'
    )
    _write(tmp_path, "bridge", properties + "<failure>private</failure>")
    assert _drain_verdict(tmp_path, "bridge")["observer_failure"] == "deadline"
    _write(
        tmp_path,
        "bridge",
        properties.replace('value="observer"', 'value="controller"')
        + "<failure>private</failure>",
    )
    assert "observer_failure" not in _drain_verdict(tmp_path, "bridge")


def test_provider_stop_cut_is_bound_to_neutral_busy_and_fixed_labels(tmp_path):
    import json

    detail = {
        "state": "observed",
        "chain": "unique_leaf",
        "masked_code": "provider_stop_receipt_unconfirmed",
        "nested_code": "neutral_busy",
        "nested_neutral_busy_origin": "gate_expired_controller",
        "nested_receipt_boundary": "reserve",
    }

    def read():
        return _drain_observations(
            "RD_QUEUE_FAILURE:"
            + json.dumps(
                {"code": "rd_provider_containment_unknown", "masked_controller": detail}
            )
        )

    assert read()["nested_neutral_busy_origin"] == "gate_expired_controller"
    assert read()["nested_receipt_boundary"] == "reserve"
    detail["nested_receipt_boundary"] = "private-canary"
    assert "nested_receipt_boundary" not in read()
    detail["nested_code"] = "neutral_transaction_lost"
    assert "nested_neutral_busy_origin" not in read()
    properties = (
        '<properties><property name="proof_phase" value="rd_waiting_release"/>'
        '<property name="rd_drain_code" value="rd_provider_containment_unknown"/>'
        '<property name="rd_drain_masked_state" value="observed"/>'
        '<property name="rd_drain_masked_chain" value="unique_leaf"/>'
        '<property name="rd_drain_nested_code" value="neutral_busy"/>'
        '<property name="rd_drain_nested_neutral_busy_origin" value="postgres_owner"/>'
        '<property name="rd_drain_nested_receipt_boundary" value="bind"/></properties>'
    )
    _write(tmp_path, "bridge", properties + "<failure>private</failure>")
    assert _drain_verdict(tmp_path, "bridge")["nested_receipt_boundary"] == "bind"
    _write(
        tmp_path,
        "bridge",
        properties.replace('value="neutral_busy"', 'value="private-canary"')
        + "<failure>private</failure>",
    )
    assert "nested_neutral_busy_origin" not in _drain_verdict(tmp_path, "bridge")


def test_masked_controller_never_projects_unbounded_or_ambiguous_nested_fields():
    import json

    def read(detail, code="rd_provider_containment_unknown"):
        return _drain_observations(
            "RD_QUEUE_FAILURE:"
            + json.dumps({"code": code, "masked_controller": detail})
        )

    detail = {
        "state": "observed",
        "chain": "unique_leaf",
        "masked_code": "analysis_process_uncertain",
        "masked_rpc_method": "execute_or_reuse",
        "masked_docker_operation": "private",
        "nested_analysis_stage": "read_journal",
        "nested_sqlstate": "55P03",
        "nested_neutral_busy_origin": "gate_expired_controller",
        "nested_receipt_boundary": "bind",
        "unexpected": "should not escape",
    }
    expected = {
        "code": "rd_provider_containment_unknown",
        "masked_state": "observed",
        "masked_chain": "unique_leaf",
        "masked_code": "analysis_process_uncertain",
        "masked_rpc_method": "execute_or_reuse",
        "nested_analysis_stage": "read_journal",
        "nested_sqlstate": "55P03",
    }
    assert read(detail) == expected
    assert read({**detail, "chain": "ambiguous"}) == {
        **{
            key: value
            for key, value in expected.items()
            if not key.startswith("nested")
        },
        "masked_chain": "ambiguous",
    }
    assert read(detail, "neutral_busy") == {"code": "neutral_busy"}
    for state in ("unavailable", "no_prior_failure"):
        assert read({**detail, "state": state}) == {
            "code": "rd_provider_containment_unknown",
            "masked_state": state,
        }


def test_junit_masked_fields_require_outer_code_and_consistent_causal_shape(tmp_path):
    def read(code, chain, state):
        properties = {
            "proof_phase": "rd_waiting_release",
            "rd_drain_code": code,
            "rd_drain_masked_state": state,
            "rd_drain_masked_chain": chain,
            "rd_drain_masked_code": "analysis_process_claim",
            "rd_drain_nested_sqlstate": "55P03",
        }
        props = "".join(
            f'<property name="{k}" value="{v}"/>' for k, v in properties.items()
        )
        _write(tmp_path, "bridge", f"<properties>{props}</properties><failure/>")
        return _drain_verdict(tmp_path, "bridge")

    masked_code = "rd_provider_containment_unknown"
    assert "nested_sqlstate" in read(masked_code, "unique_leaf", "observed")
    assert "nested_sqlstate" not in read(masked_code, "ambiguous", "observed")
    assert "masked_code" not in read(masked_code, "unique_leaf", "no_prior_failure")
    assert "masked_state" not in read("neutral_busy", "unique_leaf", "observed")


def test_provider_broker_boundary_requires_exact_masked_controller_deadline():
    import json

    detail = {
        "state": "observed",
        "chain": "single",
        "masked_rpc_method": "submit_embedding",
        "masked_code": "neutral_busy",
        "masked_neutral_busy_origin": "gate_expired_controller",
        "masked_broker_boundary": "response_publish",
    }

    def read(**overrides):
        return _drain_observations(
            "RD_QUEUE_FAILURE:"
            + json.dumps(
                {
                    "code": "rd_provider_containment_unknown",
                    "masked_controller": {**detail, **overrides},
                }
            )
        )

    assert read()["masked_broker_boundary"] == "response_publish"
    for override in (
        {"masked_broker_boundary": "private-canary"},
        {"masked_neutral_busy_origin": "gate_expired_local"},
        {"masked_rpc_method": "status"},
        {"masked_code": "neutral_transaction_lost"},
        {"chain": "ambiguous"},
    ):
        assert "masked_broker_boundary" not in read(**override)
    nested = {
        "nested_code": "neutral_busy",
        "nested_neutral_busy_origin": "gate_expired_controller",
        "nested_broker_boundary": "reserve",
    }
    assert read(chain="unique_leaf", **nested)["nested_broker_boundary"] == "reserve"
    assert "nested_broker_boundary" not in read(**nested)


def test_provider_validation_cut_requires_exact_scope_and_closed_pair():
    import json

    detail = {
        "state": "observed",
        "chain": "single",
        "masked_rpc_method": "submit_embedding",
        "masked_family": "validation",
        "masked_validation_boundary": "sdk_intent",
        "masked_validation_field": "model",
        "masked_validation_type": "value_error",
    }

    def read(**overrides):
        return _drain_observations(
            "RD_QUEUE_FAILURE:"
            + json.dumps(
                {
                    "code": "rd_provider_containment_unknown",
                    "masked_controller": {**detail, **overrides},
                }
            )
        )

    assert read()["masked_validation_boundary"] == "sdk_intent"
    assert read()["masked_validation_field"] == "model"
    assert read()["masked_validation_type"] == "value_error"
    assert (
        read(masked_rpc_method="submit_chat")["masked_validation_boundary"]
        == "sdk_intent"
    )
    assert "masked_validation_boundary" not in read(masked_rpc_method="check_control")
    assert "masked_validation_boundary" not in read(
        masked_validation_boundary="chat_response"
    )
    assert (
        read(
            masked_rpc_method="submit_chat",
            masked_validation_boundary="chat_response",
            masked_validation_field="content",
            masked_validation_type="string_too_short",
        )["masked_validation_boundary"]
        == "chat_response"
    )
    assert "masked_validation_boundary" not in read(
        masked_rpc_method="submit_chat", masked_validation_boundary="embedding_response"
    )
    assert "masked_validation_boundary" not in read(chain="ambiguous")
    assert "masked_validation_boundary" not in read(masked_family="domain")
    assert "masked_validation_field" not in read(masked_validation_boundary="private")
    assert "masked_validation_field" not in read(masked_validation_type="private")
    assert "masked_validation_type" not in read(masked_validation_field="private")


def test_exact_deadline_projects_actual_private_timing_capture(monkeypatch, capsys):
    import json
    from types import SimpleNamespace

    import pytest

    from runtimes.rdagent_operational import rpc
    from tests.rdagent.test_rpc_stage_diagnostic import Channel, _diagnostic
    from workers.research import rdagent_operational as worker

    module = _diagnostic()
    now = [10.0]
    monkeypatch.setattr(module, "monotonic", lambda: now[0])
    monkeypatch.setattr(rpc, "time", SimpleNamespace(monotonic=lambda: now[0]))
    channel = Channel()
    channel.raw = rpc.encode(
        {
            "schema_version": rpc.SCHEMA,
            "sequence": 0,
            "ticket": "private-ticket",
            "method": "submit_chat",
            "body": {"secret": "private-payload"},
        }
    )

    def handle(*_):
        now[0] = 10.501
        return {"private-reply": "secret"}

    with (
        pytest.raises(rpc.RPCError, match="^deadline$") as caught,
        module.diagnose_rd_rpc(),
    ):
        worker.serve_one(
            channel,
            sequence=0,
            deadline=10.5,
            authenticate=lambda *_: None,
            handle=handle,
        )
    assert not channel.sent
    private = capsys.readouterr().out
    assert "RD_RPC_TIMING:" in private
    fields = _drain_observations(
        "RD_QUEUE_FAILURE:"
        + json.dumps(module._queue_failure(caught.value))
        + "\n"
        + private
    )
    assert fields["rpc_auth_elapsed"] == "lt_50ms"
    assert fields["rpc_handler_elapsed"] == "lt_1s"
    assert fields["rpc_handler_remaining"] == "late_lt_250ms"
    assert fields["rpc_send_elapsed"] == "lt_50ms"
    assert fields["rpc_send_remaining"] == "late_lt_250ms"
    assert "rpc_commit_max_elapsed" not in fields
    assert "private" not in json.dumps(fields)


def test_deadline_commit_bins_require_one_send_same_rpc_and_strict_values(tmp_path):
    import json

    identity = {"method": "submit_chat", "sequence": 151, "rpc_ordinal": 333}
    commit = {
        **identity,
        "stage": "commit",
        "ok": True,
        "elapsed_ms": 500,
        "remaining_ms": -80,
    }
    send = {
        **identity,
        "stage": "send",
        "ok": False,
        "elapsed_ms": 0,
        "remaining_ms": -200,
    }
    condition = {"code": "deadline", "stage": "rpc_send", "rpc_method": "submit_chat"}

    def project(*rows, **scope):
        return _drain_observations(
            "RD_QUEUE_FAILURE:"
            + json.dumps({**condition, **scope})
            + "\nRD_RPC_TIMING:"
            + json.dumps({"tail": rows, "ignored": "private-canary"})
        )

    fields = project(commit, send)
    assert fields["rpc_commit_max_elapsed"] == "lt_1s"
    assert fields["rpc_commit_deadline_cut"] == "crossed"
    owned = {**commit, "ownership": "rpc_fenced_neutral", "unit": "provider_receipt"}
    assert project(owned, send)["rpc_late_commit_unit"] == "provider_receipt"
    for unverifiable in (
        project(owned, owned, send),
        project({**owned, "ownership": "unconfirmed"}, send),
        project({**owned, "unit": "private-canary"}, send),
    ):
        assert "rpc_late_commit_unit" not in unverifiable
    assert fields["rpc_send_remaining"] == "late_lt_250ms"
    assert not any(
        k in fields for k in ("rpc_ordinal", "sequence", "statement", "handler_input")
    )
    assert "rpc_commit_max_elapsed" not in project({**commit, "rpc_ordinal": 332}, send)
    for invalid in (
        project(commit, send, {**send, "rpc_ordinal": 444}),
        project({**commit, "elapsed_ms": True}, send),
        project({**commit, "sequence": 444}, send),
        project(commit, send, code="neutral_busy"),
    ):
        assert "rpc_send_remaining" not in invalid

    properties = "".join(
        f'<property name="rd_drain_{key}" value="{value}"/>'
        for key, value in {
            **condition,
            "rpc_send_remaining": "late_lt_250ms",
            "rpc_handler_elapsed": "private-canary",
            "rpc_late_commit_unit": "provider_receipt",
        }.items()
    )
    content = (
        '<properties><property name="proof_phase" value="rd_drain"/>'
        + properties
        + "</properties><failure>private</failure>"
    )
    _write(tmp_path, "bridge", content)
    assert _drain_verdict(tmp_path, "bridge")["rpc_send_remaining"] == "late_lt_250ms"
    assert "rpc_handler_elapsed" not in _drain_verdict(tmp_path, "bridge")
    assert "rpc_late_commit_unit" not in _drain_verdict(tmp_path, "bridge")
    for original, replacement in (
        ("deadline", "neutral_busy"),
        ("rpc_send", "transaction"),
        ("submit_chat", "check_control"),
        ("rd_drain", "rd_release_owner"),
    ):
        _write(
            tmp_path,
            "bridge",
            content.replace(f'value="{original}"', f'value="{replacement}"'),
        )
        assert "rpc_send_remaining" not in _drain_verdict(tmp_path, "bridge")


def test_oe_claim_observation_projects_exact_outcome_or_exception_only():
    from types import SimpleNamespace as NS
    from uuid import uuid4

    from sqlalchemy.exc import DBAPIError

    from script.workflow_heartbeat_production_report import _oe_claim_observation
    from workers.research.workflow_tick import TickOutcome

    class ServerFailure(Exception):
        sqlstate = "P0001"

    def call(source, value, *, state=None):
        return NS(
            excinfo=NS(
                value=value,
                traceback=[
                    NS(
                        path="tests/integration/workflow_structured_stage_fixture.py",
                        name="run_bridge",
                        lineno=len(source.splitlines()),
                        frame=NS(code=NS(firstlineno=1, source=lambda: source)),
                        locals={"oe_claim": state, "private": "private-canary"},
                    )
                ],
            )
        )

    assert _oe_claim_observation(
        call(
            'assert oe_claim.state == "structured_child_queued"',
            AssertionError("private-canary"),
            state=TickOutcome(uuid4(), "waiting_release"),
        )
    ) == {
        "oe_claim_boundary": "tick_result",
        "oe_claim_state": "waiting_release",
    }
    assert (
        _oe_claim_observation(
            call(
                'assert oe_claim.state == "structured_child_queued"',
                AssertionError("private-canary"),
                state=TickOutcome(uuid4(), "private-canary"),
            )
        )
        == {"oe_claim_boundary": "tick_result"}
    )
    assert _oe_claim_observation(
        call(
            'assert oe_claim.state == "structured_child_queued"\n'
            "with env.factory() as session:",
            RuntimeError("private-canary"),
        )
    ) == {"oe_claim_boundary": "readback_enter"}
    error = DBAPIError(
        "private-sql", {"private": "private-data"}, ServerFailure("private")
    )
    assert _oe_claim_observation(
        call(
            "oe_claim = drive_one(env.factory, env.settings, owner_id=owner, workflow_id=wid)",
            error,
        )
    ) == {
        "oe_claim_boundary": "tick_call",
        "oe_claim_error": "database",
        "oe_claim_sqlstate": "P0001",
    }
    assert _oe_claim_observation(
        call(
            "oe_claim = drive_one(env.factory, env.settings, owner_id=owner, workflow_id=wid)",
            RuntimeError("private-canary"),
        )
    ) == {"oe_claim_boundary": "tick_call", "oe_claim_error": "other"}
    assert (
        _oe_claim_observation(
            call("assert released.status_code == 200, released.text", error)
        )
        == {}
    )


def test_oe_claim_junit_never_emits_raw_or_cross_phase_values(tmp_path):
    fields = """<properties>
    <property name="proof_phase" value="oe_claim"/>
    <property name="rd_drain_oe_claim_error" value="database"/>
    <property name="rd_drain_oe_claim_sqlstate" value="P0001"/>
    <property name="rd_drain_oe_claim_site" value="dispatch"/>
    <property name="rd_drain_oe_claim_state" value="waiting_release"/>
    </properties><failure>private</failure>"""
    _write(tmp_path, "bridge", fields)
    assert _drain_verdict(tmp_path, "bridge") == {
        "proof_phase": "oe_claim",
        "oe_claim_error": "database",
        "oe_claim_sqlstate": "P0001",
        "oe_claim_site": "dispatch",
    }
    _write(tmp_path, "bridge", fields.replace('value="oe_claim"', 'value="rd_drain"'))
    assert _drain_verdict(tmp_path, "bridge") == {}


def test_oe_claim_boundary_keeps_exact_phase_and_suppresses_conflicting_details(
    tmp_path,
):
    fields = """<properties>
    <property name="proof_phase" value="oe_claim"/>
    <property name="rd_drain_oe_claim_boundary" value="readback_enter"/>
    <property name="rd_drain_oe_claim_error" value="database"/>
    <property name="rd_drain_oe_claim_sqlstate" value="23514"/>
    <property name="rd_drain_oe_claim_site" value="claim"/>
    <property name="rd_drain_oe_claim_state" value="waiting_release"/>
    </properties><failure>private</failure>"""
    _write(tmp_path, "h_bridge", fields)
    assert _drain_verdict(tmp_path, "h_bridge") == {
        "proof_phase": "oe_claim",
        "oe_claim_boundary": "readback_enter",
    }
    _write(tmp_path, "h_bridge", fields.replace('value="oe_claim"', 'value="rd_drain"'))
    assert _drain_verdict(tmp_path, "h_bridge") == {}


def test_only_attested_oe_claim_predicate_survives_junit_projection(tmp_path):
    from script.workflow_heartbeat_production_report import _OE_CLAIM_TRIGGER_LABELS

    exact_triggers = "\n".join(
        Path(f"backend/app/oe_workflow_{kind}_sql.py").read_text()
        for kind in ("graph", "reverse")
    )
    assert all(
        f"RAISE EXCEPTION '{primary}'" in exact_triggers
        for primary in _OE_CLAIM_TRIGGER_LABELS
    )
    properties = {
        "proof_phase": "oe_claim",
        "rd_drain_oe_claim_error": "database",
        "rd_drain_oe_claim_site": "dispatch",
        "rd_drain_oe_claim_sqlstate": "P0001",
        "rd_drain_oe_claim_predicate": "predecessor_input_graph",
    }

    def read(**change):
        attrs = "".join(
            f'<property name="{k}" value="{v}"/>'
            for k, v in {**properties, **change}.items()
        )
        _write(
            tmp_path,
            "bridge",
            f"<properties>{attrs}</properties><failure>private</failure>",
        )
        return _drain_verdict(tmp_path, "bridge")

    assert read()["oe_claim_predicate"] == "predecessor_input_graph"
    for change in (
        {"proof_phase": "rd_drain"},
        {"rd_drain_oe_claim_error": "validation"},
        {"rd_drain_oe_claim_sqlstate": "23514"},
        {"rd_drain_oe_claim_site": "tick"},
        {"rd_drain_oe_claim_predicate": "private-canary"},
    ):
        assert "oe_claim_predicate" not in read(**change)


def test_embedding_uses_exact_handler_return_and_unique_fenced_commit(tmp_path):
    import json

    identity = {"method": "submit_embedding", "sequence": 871, "rpc_ordinal": 991}
    condition = {
        "code": "deadline",
        "stage": "rpc_send",
        "rpc_method": "submit_embedding",
    }

    def row(stage, elapsed, remaining, **extra):
        return {
            **identity,
            "stage": stage,
            "ok": True,
            "elapsed_ms": elapsed,
            "remaining_ms": remaining,
            **extra,
        }

    auth = row("auth", 30, 900)
    crossing = row(
        "commit",
        500,
        -100,
        ownership="rpc_fenced_neutral",
        unit="provider_publication",
    )
    handler = row("handle", 1100, -200, payload="private-canary")
    sent = row("send", 0, -251, ok=False)

    def project(*tail):
        return _drain_observations(
            "RD_QUEUE_FAILURE:"
            + json.dumps(condition)
            + "\nRD_RPC_TIMING:"
            + json.dumps({"tail": tail})
        )

    result = project(
        {**crossing, "method": "submit_chat", "unit": "provider_receipt"},
        auth,
        crossing,
        handler,
        sent,
    )
    assert result["rpc_handler_remaining"] == "late_lt_250ms"
    assert result["rpc_send_remaining"] == "late_250ms_or_more"
    assert result["rpc_late_commit_unit"] == "provider_publication"
    for value in ("private-canary", "991", "871", "provider_receipt"):
        assert value not in json.dumps(result)

    failed = project(auth, {**crossing, "ok": False}, handler, sent)
    assert failed["rpc_commit_deadline_cut"] == "crossed"
    assert "rpc_late_commit_unit" not in failed
    assert "rpc_late_commit_unit" not in project(
        auth, crossing, crossing, handler, sent
    )
    assert "rpc_handler_remaining" not in project(auth, handler, crossing, sent)
    assert "rpc_handler_remaining" not in project(
        auth, handler, {**sent, "rpc_ordinal": 444}
    )

    evidence = {
        "proof_phase": "rd_waiting_release",
        **{f"rd_drain_{k}": v for k, v in result.items()},
    }
    attrs = "".join(f'<property name="{k}" value="{v}"/>' for k, v in evidence.items())
    _write(
        tmp_path,
        "bridge",
        f"<properties>{attrs}</properties><failure>private</failure>",
    )
    assert (
        _drain_verdict(tmp_path, "bridge")["rpc_handler_remaining"] == "late_lt_250ms"
    )
    assert (
        _drain_verdict(tmp_path, "bridge")["rpc_late_commit_unit"]
        == "provider_publication"
    )


def test_032h_gate_requires_both_explicit_milestones(tmp_path, monkeypatch, capsys):
    from script import workflow_heartbeat_production_report as report

    passed = ("passed", "complete", "unavailable")
    unavailable = ("unavailable",) * 3
    a = '<property name="current_032h_bridge" value="passed"/>'
    b = '<property name="current_032h_cold_restore" value="passed"/>'
    monkeypatch.setattr(report, "ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["projector", "--require-pass", "h_bridge"])

    for contents in ("", a, b, a + b + b, a + b.replace("passed", "private-canary")):
        _write(tmp_path, "h_bridge", f"<properties>{contents}</properties>")
        assert outcome(tmp_path, "h_bridge") == unavailable
        assert report.main() == 1

    path = _write(tmp_path, "h_bridge", f"<properties>{a}{b}</properties>")
    assert outcome(tmp_path, "h_bridge") == passed
    assert report.main() == 0
    path.write_text(path.read_text().replace(CASES["h_bridge"][1], CASES["bridge"][1]))
    assert report.main() == 1
    for value in ("failure", "error", "skipped"):
        _write(
            tmp_path,
            "h_bridge",
            f"<{value}>private-canary</{value}><properties>{a}{b}</properties>",
        )
        assert outcome(tmp_path, "h_bridge")[0] == value
        assert report.main() == 1
    assert capsys.readouterr().out == ""


def test_032h_full_report_preserves_historical_case_labels(
    tmp_path, monkeypatch, capsys
):
    from script import workflow_heartbeat_production_report as report

    for name in ("heartbeat", "bridge", "restore"):
        _write(tmp_path, name, "")
    _write(
        tmp_path,
        "h_bridge",
        "<failure>private-canary</failure><properties>"
        '<property name="proof_phase" value="current_cold_restore"/>'
        '<property name="proof_code" value="private-canary"/>'
        "</properties>",
    )
    monkeypatch.setattr(report, "ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["projector"])
    assert report.main() == 1
    rows = capsys.readouterr().out.splitlines()
    assert rows[1:] == [
        "RD_PRODUCTION_HEARTBEAT_V1 heartbeat=passed phase=complete code=unavailable",
        "RD_PRODUCTION_HEARTBEAT_V1 bridge=passed phase=complete code=unavailable",
        "RD_PRODUCTION_HEARTBEAT_V1 h_bridge=failure phase=current_cold_restore code=unavailable",
        "RD_PRODUCTION_HEARTBEAT_V1 restore=passed phase=complete code=unavailable",
    ]


def test_032h_scoped_projection_never_prints_historical_outcomes(
    tmp_path, monkeypatch, capsys
):
    from script import workflow_heartbeat_production_report as report

    _write(tmp_path, "bridge", "<failure>private-canary</failure>")
    _write(
        tmp_path,
        "h_bridge",
        "<failure>private-canary</failure><properties>"
        '<property name="proof_phase" value="current_cold_restore"/>'
        '<property name="proof_code" value="private-canary"/>'
        "</properties>",
    )
    monkeypatch.setattr(report, "ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["projector", "--report-h-bridge"])
    assert report.main() == 1
    assert capsys.readouterr().out.splitlines() == [
        "RD_PRODUCTION_HEARTBEAT_V1 h_bridge=failure phase=current_cold_restore code=unavailable"
    ]
    _write(
        tmp_path,
        "h_bridge",
        "<properties>"
        '<property name="current_032h_bridge" value="passed"/>'
        '<property name="current_032h_cold_restore" value="passed"/>'
        "</properties>",
    )
    assert report.main() == 0
    assert capsys.readouterr().out.splitlines() == [
        "RD_PRODUCTION_HEARTBEAT_V1 h_bridge=passed phase=complete code=unavailable"
    ]
    monkeypatch.setattr("sys.argv", ["projector", "--report-historical"])
    assert report.main() == 1
    assert "h_bridge=" not in capsys.readouterr().out


def test_032h_workflow_scoped_report_still_requires_containment_and_pg_cleanup():
    source = Path(
        ".github/workflows/mineagent-production-heartbeat-native-proof.yml"
    ).read_text()
    scope = "MINEAGENT_NATIVE_PROOF_SCOPE"
    assert f"{scope}: current-032h" in source
    for step in ("bridge", "bridge_clean", "restore_clean"):
        guard = source.split(f"        id: {step}\n", 1)[1].splitlines()[0]
        assert f"env.{scope} == 'historical'" in guard
    step = source.split(
        "- name: Report only closed 032h evidence after containment and PG cleanup\n",
        1,
    )[1]
    lines = step.splitlines()
    assert f"env.{scope} == 'current-032h'" in lines[0]
    for gate in (
        "native_setup",
        "owner_first_before",
        "heartbeat",
        "heartbeat_clean",
        "h_bridge_clean",
        "pg_cleanup",
    ):
        assert f"steps.{gate}.outcome == 'success'" in lines[0]
    assert "steps.h_bridge.outcome == 'failure'" in lines[0]
    assert "steps.bridge" not in lines[0]
    assert "steps.restore" not in lines[0]
    assert lines[2].endswith("workflow_heartbeat_production_report --report-h-bridge")


def test_h_bridge_projects_closed_sdk_outcome_at_both_boundaries(tmp_path):
    import json

    item = {
        "family": "provider",
        "code": "provider_outcome_unknown",
        "stage": "provider",
        "sqlstate": "none",
        "rpc_method": "submit_embedding",
        "sdk_outcome_cut": "wire_deadline",
        "secret_canary": "not projected",
    }
    for method, value, expected in (
        ("submit_embedding", "wire_deadline", "wire_deadline"),
        ("submit_embedding", "private-canary", None),
        ("submit_chat", "wire_deadline", None),
    ):
        item.update(rpc_method=method, sdk_outcome_cut=value)
        projected = _drain_observations("RD_QUEUE_FAILURE:" + json.dumps(item))
        assert projected["code"] == "provider_outcome_unknown"
        assert projected.get("sdk_outcome_cut") == expected
        assert "secret_canary" not in projected
        # Independently validate on the XML side, including an extraneous but
        # valid-looking field not emitted by the original stdout projector.
        projected["sdk_outcome_cut"] = value
        props = "".join(
            f'<property name="rd_drain_{key}" value="{val}"/>'
            for key, val in projected.items()
        )
        _write(
            tmp_path,
            "h_bridge",
            '<failure/><properties><property name="proof_phase" '
            f'value="rd_waiting_release"/>{props}</properties>',
        )
        assert _drain_verdict(tmp_path, "h_bridge").get("sdk_outcome_cut") == expected


def test_h_bridge_finish_evaluation_deadline_projects_only_closed_bins(tmp_path):
    import json

    condition = {
        "family": "rpc",
        "code": "deadline",
        "stage": "rpc_send",
        "rpc_method": "finish_evaluation",
    }
    identity = {"method": "finish_evaluation", "sequence": 321, "rpc_ordinal": 654}

    def row(stage, elapsed, remaining, *, ok=True):
        return {
            **identity,
            "stage": stage,
            "elapsed_ms": elapsed,
            "remaining_ms": remaining,
            "ok": ok,
        }

    def observations(tail):
        return _drain_observations(
            "RD_QUEUE_FAILURE:"
            + json.dumps(condition)
            + "\nRD_RPC_TIMING:"
            + json.dumps({"tail": tail})
        )

    auth = row("auth", 20, 600)
    commit = row("commit", 40, 100)
    handler = row("handle", 700, -100)
    send = row("send", 0, -110, ok=False)
    data = observations((auth, commit, handler, send))
    assert data["rpc_auth_elapsed"] == "lt_50ms"
    assert data["rpc_handler_elapsed"] == "lt_1s"
    assert data["rpc_handler_remaining"] == "late_lt_250ms"
    assert data["rpc_commit_deadline_cut"] == "not_observed_past_deadline"
    assert data["rpc_send_remaining"] == "late_lt_250ms"
    assert "sequence" not in data and "rpc_ordinal" not in data
    assert "rpc_send_remaining" not in observations(
        (auth, commit, handler, send, {**send, "rpc_ordinal": 655})
    )

    props = "".join(
        f'<property name="rd_drain_{key}" value="{value}"/>'
        for key, value in data.items()
    )
    _write(
        tmp_path,
        "h_bridge",
        '<failure/><properties><property name="proof_phase" '
        f'value="rd_waiting_release"/>{props}</properties>',
    )
    verdict = _drain_verdict(tmp_path, "h_bridge")
    assert verdict["rpc_handler_remaining"] == "late_lt_250ms"
    assert verdict["rpc_send_remaining"] == "late_lt_250ms"
