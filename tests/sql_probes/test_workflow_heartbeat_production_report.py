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
    for state, cut in (
        ("structured_child_queued", "oe_claim"),
        ("child_owned_by_engine_worker", "oe_queue_boundary"),
    ):
        comparison = next(
            i
            for i, line in enumerate(source.splitlines(), 1)
            if line.strip() == f'== "{state}"'
        )
        assert _bridge_phase(source, comparison - 2) == cut


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
    assert "masked_validation_boundary" not in read(masked_rpc_method="submit_chat")
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
        ("submit_chat", "submit_embedding"),
        ("rd_drain", "rd_release_owner"),
    ):
        _write(
            tmp_path,
            "bridge",
            content.replace(f'value="{original}"', f'value="{replacement}"'),
        )
        assert "rpc_send_remaining" not in _drain_verdict(tmp_path, "bridge")
