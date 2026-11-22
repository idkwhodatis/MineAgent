"""Source contract only; no claim of completed graphs or native acceptance.

The independent job repeats the real finite-workflow gate without depending on
an earlier unrelated acceptance step. Its source equality is not runtime proof.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ORIGINAL = ROOT / ".github/workflows/mineagent-structured-factor.yml"
INDEPENDENT = ROOT / ".github/workflows/mineagent-workflow-populated-proof.yml"
FIRST_GATE = (
    "Require twenty actual units across live and independent cold restore cases"
)
TARGET = "Require actual finite workflow RD to OE stage and complete restore"
CLEANUP = "Confirm exact normal OE controller units are absent"
OLD_ARTIFACT = "mineagent-structured-factor-${{ github.sha }}"
NEW_ARTIFACT = "mineagent-workflow-populated-baseline-${{ github.sha }}"


def steps(source):
    body = source.split("    steps:\n", 1)[1]
    parts = re.split(r"(?=^      - )", body, flags=re.MULTILINE)
    assert not parts[0].strip()
    return parts[1:]


def named(items, name):
    matches = [
        i for i, item in enumerate(items) if item.startswith(f"      - name: {name}\n")
    ]
    assert len(matches) == 1, f"Missing or repeated step: {name}"
    return matches[0]


def test_exact_original_setup_target_and_cleanup_are_preserved():
    original, independent = steps(ORIGINAL.read_text()), steps(INDEPENDENT.read_text())
    setup_end = named(original, FIRST_GATE)
    target = named(original, TARGET)
    cleanup_start = named(original, CLEANUP)
    assert setup_end == 22 and cleanup_start > target
    expected = original[:setup_end] + [original[target]] + original[cleanup_start:]
    expected[-1] = expected[-1].replace(OLD_ARTIFACT, NEW_ARTIFACT)
    # One additional source-only guard runs after the unchanged host installation.
    source_guard = (
        "      - name: Require independent baseline source contract\n"
        "        run: python -m pytest tests/sql_probes/test_workflow_populated_proof.py tests/rdagent/test_rpc_stage_diagnostic.py tests/sql_probes/test_rd_completed_graph_probe_source.py tests/sql_probes/test_workflow_heartbeat_lock_order_source.py tests/sql_probes/test_heartbeat_schema_inventory_source.py tests/sql_probes/test_rd_attempt_admission_candidate.py tests/sql_probes/test_workflow_heartbeat_admission_source.py tests/sql_probes/test_rd_admission_expiry_observation_unit.py tests/sql_probes/test_rd_admission_expiry_observation_source.py tests/sql_probes/test_rd_admission_expiry_roles_unit.py tests/sql_probes/test_rd_ticketed_admission_candidate.py tests/sql_probes/test_rd_native_admission_trial.py tests/sql_probes/test_rd_timeline_diagnostic.py tests/sql_probes/test_reverse_heartbeat_candidate_source.py -q\n"
    )
    install = named(expected, "Install unchanged host test closure")
    expected.insert(install + 1, source_guard)
    readiness = (
        "      - name: Record successful native baseline setup\n"
        "        id: native_setup\n"
        '        run: echo "Native baseline setup complete"\n'
    )
    clean = (
        "      - name: Require clean native units before current schema baseline\n"
        "        id: current_baseline_containment\n"
        "        if: ${{ !cancelled() && steps.native_setup.outcome == 'success' }}\n"
        "        shell: bash\n"
        "        run: |\n"
        "          set -euo pipefail\n"
        + "".join(
            step.split("        run: |\n", 1)[1]
            for step in original[cleanup_start : cleanup_start + 6]
        )
    )
    current = (
        original[target]
        .replace(TARGET, "Require actual current032g finite workflow baseline", 1)
        .replace(
            "        timeout-minutes: 40\n",
            "        if: ${{ !cancelled() && steps.native_setup.outcome == 'success' && steps.current_baseline_containment.outcome == 'success' }}\n"
            "        timeout-minutes: 40\n",
            1,
        )
        .replace(
            '          MINEAGENT_WORKFLOW_STRUCTURED_FLOW_REQUIRED: "1"\n',
            '          MINEAGENT_WORKFLOW_CURRENT_HEAD_REQUIRED: "1"\n',
            1,
        )
        .replace(
            "tests/integration/test_workflow_structured_stage_flow.py",
            "tests/integration/test_workflow_current_head_baseline.py",
        )
        .replace(
            "workflow-structured-stage",
            "workflow-current032g-baseline",
        )
        .replace(
            "test_actual_final_rd_checkpoint_drives_one_oe_stage_and_exact031_restore",
            "test_actual_current032g_workflow_completes_with_readonly_graph_checks",
        )
    )
    heartbeat_order = """      - name: Require real finite workflow heartbeat lock order evidence
        if: ${{ !cancelled() && steps.native_setup.outcome == 'success' && steps.current_baseline_containment.outcome == 'success' }}
        timeout-minutes: 10
        shell: bash
        env:
          MINEAGENT_HEARTBEAT_ORDER_REQUIRED: "1"
        run: |
          set -euo pipefail
          export MINEAGENT_CANDIDATE_IMAGE="$(cat var/numeric-proof/candidate.iid)"
          export MINEAGENT_NORMALIZER_IMAGE="$(cat var/numeric-proof/normalizer.iid)"
          export MINEAGENT_ORCHESTRATOR_IMAGE="$(cat var/numeric-proof/orchestrator.iid)"
          export MINEAGENT_ANALYSIS_IMAGE="$(cat var/numeric-proof/analysis.iid)"
          export MINEAGENT_QLIB_INSTALLED_IMAGE="$(cat var/numeric-proof/qlib-installed.iid)"
          export MINEAGENT_OE_STRUCTURED_RUNTIME_PROFILE="$(cat var/numeric-proof/oe-normal/oe-structured-runtime-profile.json)"
          python -m pytest tests/integration/test_workflow_heartbeat_lock_order.py --collect-only -q -p no:cacheprovider > var/numeric-private/heartbeat-order-collection.txt
          python -m pytest tests/integration/test_workflow_heartbeat_lock_order.py -q -p no:cacheprovider --junitxml=var/numeric-private/heartbeat-order.xml
          python script/sql_candidate_proof.py --group heartbeat_order --collection var/numeric-private/heartbeat-order-collection.txt --junit var/numeric-private/heartbeat-order.xml --output var/numeric-proof/heartbeat-order-outcomes.json
"""
    heartbeat_admission = (
        heartbeat_order.replace(
            "Require real finite workflow heartbeat lock order evidence",
            "Require isolated local admission candidate evidence",
        )
        .replace("timeout-minutes: 10", "timeout-minutes: 20")
        .replace(
            "MINEAGENT_HEARTBEAT_ORDER_REQUIRED",
            "MINEAGENT_HEARTBEAT_ADMISSION_CANDIDATE_REQUIRED",
        )
        .replace(
            "test_workflow_heartbeat_lock_order.py",
            "test_workflow_heartbeat_admission_candidate.py",
        )
        .replace("heartbeat-order", "heartbeat-admission")
        .replace("--group heartbeat_order", "--group heartbeat_admission")
    )
    heartbeat_observation = (
        heartbeat_admission.replace(
            "Require isolated local admission candidate evidence",
            "Require isolated committed expiry observation evidence",
        )
        .replace(
            "test_workflow_heartbeat_admission_candidate.py",
            "test_rd_admission_expiry_observation.py",
        )
        .replace("heartbeat-admission", "heartbeat-observation")
        .replace("--group heartbeat_admission", "--group heartbeat_observation")
    )
    heartbeat_roles = (
        heartbeat_observation.replace(
            "Require isolated committed expiry observation evidence",
            "Require original heartbeat cancellation role evidence",
        )
        .replace(
            "test_rd_admission_expiry_observation.py",
            "test_rd_admission_expiry_roles.py",
        )
        .replace("heartbeat-observation", "heartbeat-roles")
        .replace("--group heartbeat_observation", "--group heartbeat_roles")
    )
    heartbeat_admission = heartbeat_admission.replace(
        "      - name: Require isolated local admission candidate evidence\n",
        "      - name: Require isolated local admission candidate evidence\n        id: heartbeat_admission\n",
    )
    heartbeat_observation = heartbeat_observation.replace(
        "      - name: Require isolated committed expiry observation evidence\n",
        "      - name: Require isolated committed expiry observation evidence\n        id: heartbeat_observation\n",
    )
    heartbeat_roles = heartbeat_roles.replace(
        "      - name: Require original heartbeat cancellation role evidence\n",
        "      - name: Require original heartbeat cancellation role evidence\n        id: heartbeat_roles\n",
    )
    reverse_heartbeat = (
        heartbeat_roles.replace(
            "Require original heartbeat cancellation role evidence",
            "Require isolated reverse heartbeat event candidate",
        )
        .replace("        id: heartbeat_roles\n", "")
        .replace(
            "test_rd_admission_expiry_roles.py", "test_reverse_heartbeat_candidate.py"
        )
        .replace("heartbeat-roles", "reverse-heartbeat")
        .replace("--group heartbeat_roles", "--group reverse_heartbeat")
        .replace(
            '          MINEAGENT_HEARTBEAT_ADMISSION_CANDIDATE_REQUIRED: "1"',
            '          MINEAGENT_REVERSE_HEARTBEAT_CANDIDATE_REQUIRED: "1"\n          MINEAGENT_HEARTBEAT_ADMISSION_CANDIDATE_REQUIRED: "1"',
        )
    )
    trial_clean = clean.replace(
        "Require clean native units before current schema baseline",
        "Require clean native units before isolated scheduling trial",
    ).replace("id: current_baseline_containment", "id: native_trial_containment")
    native_trial = (
        current.replace(
            "Require actual current032g finite workflow baseline",
            "Require isolated actual workflow scheduling trial",
        )
        .replace(
            "steps.current_baseline_containment.outcome == 'success'",
            "steps.native_trial_containment.outcome == 'success' && steps.heartbeat_admission.outcome == 'success' && steps.heartbeat_observation.outcome == 'success' && steps.heartbeat_roles.outcome == 'success'",
        )
        .replace(
            '          MINEAGENT_WORKFLOW_CURRENT_HEAD_REQUIRED: "1"',
            '          MINEAGENT_TICKETED_NATIVE_TRIAL_REQUIRED: "1"\n          MINEAGENT_WORKFLOW_CURRENT_HEAD_REQUIRED: "1"',
        )
        .replace(
            "test_workflow_current_head_baseline.py",
            "test_workflow_ticketed_admission_trial.py",
        )
        .replace("workflow-current032g-baseline", "workflow-ticketed-native-trial")
        .replace(
            "test_actual_current032g_workflow_completes_with_readonly_graph_checks",
            "test_actual_current032g_workflow_with_ticketed_admission_trial",
        )
        .replace(
            "          status=0\n",
            "          python -m pytest tests/integration/test_workflow_ticketed_admission_trial.py --collect-only -q -p no:cacheprovider > var/numeric-private/heartbeat-native-collection.txt\n          status=0\n",
        )
        .replace(
            '          test "$status" = 0\n',
            '          test "$status" = 0\n          python script/sql_candidate_proof.py --group heartbeat_native --collection var/numeric-private/heartbeat-native-collection.txt --junit var/numeric-private/workflow-ticketed-native-trial.xml --output var/numeric-proof/heartbeat-native-outcomes.json\n',
        )
    )
    target_index = named(expected, TARGET)
    expected[target_index : target_index + 1] = [
        readiness,
        expected[target_index],
        clean,
        heartbeat_order,
        heartbeat_admission,
        heartbeat_observation,
        heartbeat_roles,
        reverse_heartbeat,
        current,
        trial_clean,
        native_trial,
    ]
    # Full step bytes, including command, env, timeout, if and artifact policy.
    assert independent == expected
    assert len(independent) == 42
    assert named(independent, TARGET) == setup_end + 2


def test_job_environment_runner_and_deadlines_are_unchanged():
    def job_header(source):
        header = source.split("jobs:\n", 1)[1].split("    steps:\n", 1)[0]
        # Only job identifier and explanatory comments differ.
        return [
            line
            for line in header.splitlines()[1:]
            if not line.lstrip().startswith("#")
        ]

    assert job_header(INDEPENDENT.read_text()) == job_header(ORIGINAL.read_text())
    source = INDEPENDENT.read_text()
    assert "    timeout-minutes: 120\n" in source
    target = steps(source)[named(steps(source), TARGET)]
    assert "        timeout-minutes: 40\n" in target
    assert "test_workflow_structured_stage_flow.py" in target
    assert "assert len(cases)==1" in target
    assert (
        "test_actual_final_rd_checkpoint_drives_one_oe_stage_and_exact031_restore"
        in target
    )
    assert "('failure','error','skipped')" in target
    assert 'test "$status" = 0' in target
    assert "        if:" not in target


def test_independent_baseline_does_not_activate_candidate_or_weaken_security():
    source = INDEPENDENT.read_text()
    for required in (
        "independent populated workflow baseline",
        "not full production acceptance",
        "persist-credentials: false",
        "contents: read",
        'E2E_LIVE_PROVIDERS: "0"',
        'MODEL_PROFILES_ENABLED: "false"',
        "--require-hashes",
        "--publish 127.0.0.1:5432:5432",
        "Generate masked disposable database credentials",
        "path: var/numeric-proof/",
        "if-no-files-found: error",
        NEW_ARTIFACT,
    ):
        assert required in source
    for forbidden in (
        "continue-on-error",
        "pull_request_target",
        "secrets.",
        "docker push",
        "oe_trigger_target_probe",
        "render_candidate_sql",
        "DISABLE TRIGGER",
        "fsync=off",
        "synchronous_commit=off",
        "full_page_writes=off",
        "--deselect",
        "--ignore",
        "--maxfail",
        " -k ",
        "path: var/numeric-private",
        "contents: write",
        "actions: write",
    ):
        assert forbidden not in source
    assert source.count("if: always()") == 8
    assert "name: " + OLD_ARTIFACT not in source


def test_baseline_trigger_paths_cover_proof_and_relevant_inputs():
    source = INDEPENDENT.read_text()
    trigger = source.split("permissions:\n", 1)[0]
    assert "  workflow_dispatch:\n" in trigger
    assert "    branches: [master]\n" in trigger
    paths = re.findall(r'^      - "([^"]+)"$', trigger, flags=re.MULTILINE)
    assert paths == [
        ".github/workflows/mineagent-workflow-populated-proof.yml",
        ".github/workflows/mineagent-structured-factor.yml",
        "tests/sql_probes/test_workflow_populated_proof.py",
        "backend/alembic/**",
        "backend/app/*sql.py",
        "tests/integration/*workflow*",
        "tests/integration/oe_structured*",
        "tests/integration/rd_configured*",
        "script/ci_pg_client.py",
        "tests/integration/rd_completed_graph_probe.py",
        "tests/sql_probes/test_rd_completed_graph_probe_source.py",
        "tests/sql_probes/test_heartbeat_schema_inventory_source.py",
        "tests/sql_probes/test_rd_attempt_admission_candidate.py",
        "tests/sql_probes/test_workflow_heartbeat_admission_source.py",
        "tests/integration/test_rd_admission_expiry_observation.py",
        "tests/sql_probes/rd_admission_expiry_observation.py",
        "tests/sql_probes/test_rd_admission_expiry_observation_unit.py",
        "tests/sql_probes/test_rd_admission_expiry_observation_source.py",
        "tests/integration/test_rd_admission_expiry_roles.py",
        "tests/sql_probes/test_rd_admission_expiry_roles_unit.py",
        "tests/sql_probes/rd_ticketed_admission_candidate.py",
        "tests/sql_probes/test_rd_ticketed_admission_candidate.py",
        "tests/sql_probes/rd_native_admission_trial.py",
        "tests/sql_probes/test_rd_native_admission_trial.py",
        "tests/sql_probes/test_rd_timeline_diagnostic.py",
        "tests/integration/rd_timeline_diagnostic.py",
        "tests/integration/rd_owner_lock_diagnostic.py",
        "tests/integration/test_reverse_heartbeat_candidate.py",
        "tests/sql_probes/reverse_heartbeat_candidate.py",
        "tests/sql_probes/reverse_heartbeat_candidate_pins.json",
        "tests/sql_probes/test_reverse_heartbeat_candidate_source.py",
        "tests/sql_probes/rd_attempt_admission_candidate.py",
        "tests/sql_probes/test_workflow_heartbeat_lock_order_source.py",
        "tests/structured/sql_candidate_inventory.json",
        "script/sql_candidate_proof.py",
    ]


# These tests execute only the isolated helper AST with injected diagnostic
# dependencies. They cannot run a queue, invoke a provider or fabricate a child.
def diagnostic_function():
    import ast

    source = (
        ROOT / "tests/integration/workflow_structured_stage_fixture.py"
    ).read_text()
    tree = ast.parse(source)
    return next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "diagnose_first_rd_drain"
    )


def test_diagnostic_wraps_only_original_first_drain_without_changing_assertions():
    import ast
    import hashlib

    source = (
        ROOT / "tests/integration/workflow_structured_stage_fixture.py"
    ).read_text()
    tree = ast.parse(source)
    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    drain = functions["drain"]
    assert hashlib.sha256(
        ast.dump(drain, include_attributes=False).encode()
    ).hexdigest() == (
        "0e5b38c186850a3d3baea94904946dd7244d0b3298fa2d1ac8e2afd437055137"
    )
    bridge = functions["run_bridge"]
    wrappers = [
        n
        for n in ast.walk(bridge)
        if isinstance(n, ast.With)
        and any(
            isinstance(i.context_expr, ast.Call)
            and isinstance(i.context_expr.func, ast.Name)
            and i.context_expr.func.id == "diagnose_first_rd_drain"
            for i in n.items
        )
    ]
    assert len(wrappers) == 1
    wrapper = wrappers[0]
    assert len(wrapper.items) == len(wrapper.body) == 1
    assert (
        ast.unparse(wrapper.items[0].context_expr)
        == "diagnose_first_rd_drain(env.factory, engine, wid)"
    )
    assert ast.unparse(wrapper.body[0]) == "drain(env.factory, env.settings)"
    index = bridge.body.index(wrapper)
    bridge.body[index : index + 1] = wrapper.body
    capture, comparison = ast.parse(
        "oe_claim = drive_one(env.factory, env.settings, owner_id=owner, workflow_id=wid)\n"
        'assert oe_claim.state == "structured_child_queued"'
    ).body
    shapes = [ast.dump(n, include_attributes=False) for n in (capture, comparison)]
    locations = [
        i
        for i in range(len(bridge.body) - 1)
        if [ast.dump(n, include_attributes=False) for n in bridge.body[i : i + 2]]
        == shapes
    ]
    assert len(locations) == 1
    assert (
        sum(isinstance(n, ast.Name) and n.id == "oe_claim" for n in ast.walk(bridge))
        == 2
    )
    index = locations[0]
    bridge.body[index : index + 2] = ast.parse(
        'assert drive_one(env.factory, env.settings, owner_id=owner, workflow_id=wid).state == "structured_child_queued"'
    ).body
    assert hashlib.sha256(
        ast.dump(bridge, include_attributes=False).encode()
    ).hexdigest() == (
        "64ae04787212068db06db72b0fd053ae193c1c54ea1bf0f0c711ca1d299f9146"
    )
    helper = ast.unparse(diagnostic_function())
    assert (
        "select(FactorWorkflowSubmission.run_id).filter_by(workflow_id=workflow_id).limit(2)"
        in helper
    )
    assert "len(ids) == 1 and isinstance(ids[0], UUID)" in helper
    assert "stage='workflow_first'" in helper
    for forbidden in ("print(", "session.add(", "session.commit(", "session.delete("):
        assert forbidden not in helper


def _injected_diagnostic(monkeypatch, mode):
    import ast
    import sys
    from contextlib import ExitStack, contextmanager
    from types import ModuleType, SimpleNamespace
    from unittest.mock import patch
    from uuid import UUID

    events = []
    run_id, workflow_id = UUID(int=1), UUID(int=2)
    worker = SimpleNamespace(execute=lambda: events.append("original-callback"))
    original = worker.execute

    def install(name, **attrs):
        module = ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)

    class Query:
        def filter_by(self, **kwargs):
            assert kwargs == {"workflow_id": workflow_id}
            events.append("scoped-query")
            return self

        def limit(self, value):
            assert value == 2
            events.append("bounded-query")
            return self

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            events.append("session-close")

        def scalars(self, statement):
            if mode == "lookup":
                raise RuntimeError("PRIVATE_LOOKUP")
            return self

        def all(self):
            if mode == "ambiguous":
                return [run_id, UUID(int=3)]
            if mode == "malformed":
                return ["PRIVATE_ID"]
            return [run_id]

        def get(self, model, value):
            assert value == run_id
            if mode == "state-query":
                raise RuntimeError("PRIVATE_STATE")
            return "closed-state"

    @contextmanager
    def pg(engine, *, stage):
        assert stage == "workflow_first"
        events.append("pg-enter")
        if mode == "pg-enter":
            raise RuntimeError("PRIVATE_PG")
        try:
            yield
        finally:
            events.append("pg-exit")
            if mode == "pg-exit":
                raise RuntimeError("PRIVATE_PG")

    @contextmanager
    def queue(factory, value):
        assert value == run_id
        events.append("queue-enter")
        if mode == "queue-enter":
            raise RuntimeError("PRIVATE_QUEUE")
        with patch.object(worker, "execute", lambda: events.append("patched-callback")):
            try:
                yield
            finally:
                events.append("queue-exit")
                if mode == "queue-exit":
                    raise RuntimeError("PRIVATE_QUEUE")

    def report(session, run):
        assert run == "closed-state"
        events.append("report")
        if mode == "report":
            raise RuntimeError("PRIVATE_REPORT")

    install(
        "backend.app.rdagent_workflow_models",
        FactorWorkflowSubmission=SimpleNamespace(run_id="run-id-column"),
    )
    install("backend.app.models", ResearchRun=object())
    install("rd_pg_diagnostic", diagnose_provider_functions=pg)
    install("rd_rpc_diagnostic", diagnose_rd_queue=queue)

    @contextmanager
    def lease_observer():
        events.append("lease-enter")
        try:
            yield
        finally:
            events.append("lease-exit")

    install("rd_lease_diagnostic", diagnose_attempt_lease=lease_observer)
    install("normal_rd_fixture_diagnostic", report_normal_rd_state=report)
    namespace = {
        "ExitStack": ExitStack,
        "contextmanager": contextmanager,
        "UUID": UUID,
        "select": lambda column: Query(),
    }
    module = ast.Module(body=[diagnostic_function()], type_ignores=[])
    exec(  # noqa: S102 -- compile only this repository's isolated fixture helper AST
        compile(ast.fix_missing_locations(module), "<diagnostic-helper>", "exec"),
        namespace,
    )
    return (
        namespace["diagnose_first_rd_drain"](Session, object(), workflow_id),
        events,
        worker,
        original,
    )


# Import here to keep the original source-contract dependency closure explicit.
import pytest


@pytest.mark.parametrize(
    "mode",
    [
        "ok",
        "lookup",
        "ambiguous",
        "malformed",
        "pg-enter",
        "queue-enter",
        "pg-exit",
        "queue-exit",
        "state-query",
        "report",
    ],
)
@pytest.mark.parametrize("worker_fails", [False, True])
def test_diagnostics_never_replace_retry_or_suppress_worker(
    monkeypatch, capsys, mode, worker_fails
):
    context, events, worker, original = _injected_diagnostic(monkeypatch, mode)
    failure = RuntimeError("PRIVATE_WORKER_FAILURE")
    caught = None
    try:
        with context:
            events.append("drain")
            worker.execute()
            if worker_fails:
                raise failure
    except RuntimeError as error:
        caught = error
    assert caught is (failure if worker_fails else None)
    assert events.count("drain") == 1
    assert events.count("lease-enter") == events.count("lease-exit") == 1
    assert (
        events.index("lease-enter") < events.index("drain") < events.index("lease-exit")
    )
    assert worker.execute is original  # Patches restore even if worker/exit fails.
    assert "PRIVATE" not in capsys.readouterr().out
    if mode not in {"lookup", "ambiguous", "malformed", "queue-enter"}:
        assert events.count("queue-exit") == 1
        assert events.index("drain") < events.index("queue-exit")
    if "report" in events:
        assert events.index("drain") < events.index("report")
        if "queue-exit" in events:
            assert events.index("queue-exit") < events.index("report")


@pytest.mark.parametrize("worker_fails", [False, True])
@pytest.mark.parametrize("print_failure", [OSError, KeyboardInterrupt])
def test_queue_diagnostic_print_failure_preserves_callback_and_restores_patch(
    monkeypatch, worker_fails, print_failure
):
    from runtimes.rdagent_operational import rpc
    from tests.rdagent.test_rpc_stage_diagnostic import _diagnostic
    from workers.research import rdagent_operational as worker
    from workers.research import rdagent_queue

    module = _diagnostic()
    result = object()
    original_error = RuntimeError("private-worker-error")
    calls = []
    emissions = []

    def execute(*args, **kwargs):
        calls.append((args, kwargs))
        if worker_fails:
            raise original_error
        return result

    def broken_print(message, **kwargs):
        assert kwargs == {"flush": True}
        assert message.startswith(("RD_QUEUE_FAILURE:", "RD_QUEUE_STATE:"))
        assert "private" not in message
        emissions.append(message.split(":", 1)[0])
        raise print_failure("private-output-error")

    monkeypatch.setattr(rdagent_queue, "execute", execute)
    monkeypatch.setattr(module, "print", broken_print, raising=False)
    monkeypatch.setattr(
        module, "_queue_state", lambda *_: {"status": "diagnostic_unavailable"}
    )
    original_serve, original_receive, original_send = (
        worker.serve_one,
        rpc.receive,
        rpc.send,
    )
    caught = None
    observed = None
    try:
        with module.diagnose_rd_queue(object(), object()):
            observed = rdagent_queue.execute("argument", key="value")
    except RuntimeError as error:
        caught = error
    assert caught is (original_error if worker_fails else None)
    assert observed is (None if worker_fails else result)
    assert calls == [(("argument",), {"key": "value"})]
    assert emissions == (
        ["RD_QUEUE_FAILURE", "RD_QUEUE_STATE"] if worker_fails else ["RD_QUEUE_STATE"]
    )
    assert rdagent_queue.execute is execute
    assert (worker.serve_one, rpc.receive, rpc.send) == (
        original_serve,
        original_receive,
        original_send,
    )


@pytest.mark.parametrize(
    "code",
    [
        "provider_input_rejected",
        "provider_admission_unconfirmed",
        "provider_identity_receipt_unconfirmed",
        "provider_stop_receipt_unconfirmed",
        "attempt_fenced",
    ],
)
def test_first_rd_failure_codes_are_closed_and_preserve_private_unknowns(code):
    from backend.app.services import DomainError
    from packages.rdagent_provider.transport import ProviderFailure
    from tests.rdagent.test_rpc_stage_diagnostic import _diagnostic

    error_type = DomainError if code == "attempt_fenced" else ProviderFailure

    def error(value):
        return (
            DomainError(value, "private diagnostic detail", 409)
            if error_type is DomainError
            else ProviderFailure(value)
        )

    module = _diagnostic()
    assert module._queue_failure(error(code))["code"] == code
    assert (
        module._queue_failure(error("private credential and arbitrary code"))["code"]
        == "unknown"
    )


def test_current_native_baseline_is_real_separate_and_readonly_after_bridge():
    import ast

    path = ROOT / "tests/integration/test_workflow_current_head_baseline.py"
    source = path.read_text()
    tree = ast.parse(source)
    tests = [
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")
    ]
    assert len(tests) == 1
    assert (
        tests[0].name
        == "test_actual_current032g_workflow_completes_with_readonly_graph_checks"
    )
    assert [arg.arg for arg in tests[0].args.args] == [
        "drill_databases",
        "tmp_path",
        "monkeypatch",
    ]
    for required in (
        "MINEAGENT_WORKFLOW_CURRENT_HEAD_REQUIRED",
        "legacy._engine(drill_databases.source)",
        'legacy._upgrade(source, "032g_schema_keyset_cost")',
        'storage = tmp_path / "current032g-workflow-source"',
        "with actual_environment(source, settings, monkeypatch) as env:",
        "completed = run_bridge(env, source, tmp_path)",
        "SET TRANSACTION READ ONLY",
        "SELECT version_num FROM alembic_version",
        "SELECT public.mineagent_rd_provider_graph(:rid)",
        "SELECT public.mineagent_oes_graph_check(:rid)",
        "completed.rd.child_run_id",
        "completed.oe_run_id",
        "completed.workflow_id",
        "source.dispose()",
        "assert (len(env.rd_calls), len(env.oe_calls)) == call_counts",
    ):
        assert required in source
    assert source.index("run_bridge(env, source, tmp_path)") < source.index(
        "SET TRANSACTION READ ONLY"
    )
    for forbidden in (
        "render_candidate_sql",
        "oe_trigger_target_probe",
        "session.add",
        "statement_timeout",
        "lock_timeout",
        "pytest.skip",
        "assert_restore(",
        "INSERT INTO",
        "UPDATE ",
        "DELETE FROM",
    ):
        assert forbidden not in source
    assert (
        source.count("legacy._upgrade(")
        == source.count("run_bridge(env, source, tmp_path)")
        == 1
    )


def test_current_native_probe_readiness_and_all_six_absence_checks_are_required():
    items = steps(INDEPENDENT.read_text())
    ready = items[named(items, "Record successful native baseline setup")]
    absence = items[
        named(items, "Require clean native units before current schema baseline")
    ]
    current = items[named(items, "Require actual current032g finite workflow baseline")]
    assert "        if:" not in ready  # Default success requires every prior setup.
    assert "id: native_setup" in ready
    assert "!cancelled() && steps.native_setup.outcome == 'success'" in absence
    assert (
        "!cancelled() && steps.native_setup.outcome == 'success' && steps.current_baseline_containment.outcome == 'success'"
        in current
    )
    assert absence.count("docker ps --all --quiet --no-trunc") == 6
    assert "docker rm" not in absence
    assert "assert len(cases)==1" in current
    assert "('failure','error','skipped')" in current
    assert "var/numeric-private/workflow-current032g-baseline.xml" in current
    assert "var/numeric-proof/workflow-current032g-baseline-outcomes.xml" in current
    assert 'test "$status" = 0' in current
    assert named(items, "Record successful native baseline setup") < named(
        items, TARGET
    )
    assert (
        named(items, TARGET)
        < named(items, "Require clean native units before current schema baseline")
        < named(items, "Require actual current032g finite workflow baseline")
        < named(items, CLEANUP)
    )


def test_current_baseline_preserves_original_pre_admission_control_history():
    original = (
        ROOT / "tests/integration/test_workflow_structured_stage_flow.py"
    ).read_text()
    current = (
        ROOT / "tests/integration/test_workflow_current_head_baseline.py"
    ).read_text()

    def controls(source):
        return source.split(
            "        with actual_environment(source, settings, monkeypatch) as env:\n",
            1,
        )[1].split("            completed = run_bridge(env, source, tmp_path)", 1)[0]

    assert controls(current) == controls(original)
    assert "assert stale.status_code == 409" in controls(current)
    assert "assert env.rd_calls == env.oe_calls == []" in controls(current)


def _lease_diagnostic(monkeypatch):
    import importlib.util

    monkeypatch.syspath_prepend(str(ROOT / "tests/integration"))
    path = ROOT / "tests/integration/rd_lease_diagnostic.py"
    spec = importlib.util.spec_from_file_location("rd_lease_diagnostic_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _lease_object():
    from threading import Event
    from types import SimpleNamespace

    return SimpleNamespace(lost=Event(), cancelled=Event(), last_pulse=0.0)


def test_lease_observer_preserves_call_result_failure_state_and_patch(
    monkeypatch, capsys
):
    import json

    from backend.app.services import DomainError
    from workers.research.lease import AttemptLease

    module = _lease_diagnostic(monkeypatch)
    lease, calls, result = _lease_object(), [], object()
    error = DomainError("attempt_fenced", "PRIVATE_OWNER_TOKEN", 409)
    times = iter([10.0, 10.1, 20.0, 20.4])
    monkeypatch.setattr(module, "monotonic", lambda: next(times))

    def original(instance, *args, **kwargs):
        assert instance is lease
        calls.append((args, kwargs))
        if len(calls) == 1:
            instance.last_pulse = 10.1
            return result
        instance.lost.set()
        instance.cancelled.set()
        raise error

    monkeypatch.setattr(AttemptLease, "pulse", original)
    with module.diagnose_attempt_lease():
        assert AttemptLease.pulse(lease, True, extra="opaque") is result
        with pytest.raises(DomainError) as caught:
            AttemptLease.pulse(lease, force=True)
        assert caught.value is error
        assert capsys.readouterr().out == ""  # No heartbeat-thread stdout.
    assert AttemptLease.pulse is original
    assert calls == [((True,), {"extra": "opaque"}), ((), {"force": True})]
    output = capsys.readouterr().out
    assert "PRIVATE" not in output and "opaque" not in output
    report = json.loads(output.removeprefix("RD_LEASE_PULSES_V1:"))
    assert report["successful_pulse_returns"] == report["failed_pulse_calls"] == 1
    assert report["observed_total_ms"] == 500
    assert report["observed_max_ms"] == 400
    assert report["failures"] == [
        {
            "family": "domain",
            "code": "attempt_fenced",
            "sqlstate": "none",
            "lost": True,
            "cancelled": True,
            "elapsed_ms": 400,
            "last_success_age_ms": 10300,
            "database_guard": "none",
            "successful_pulse_returns": 1,
        }
    ]


@pytest.mark.parametrize("worker_fails", [False, True])
@pytest.mark.parametrize("diagnostic_failure", ["clock", "classifier", "print"])
def test_lease_observer_failures_never_change_worker_outcome(
    monkeypatch, worker_fails, diagnostic_failure
):
    from workers.research.lease import AttemptLease

    module = _lease_diagnostic(monkeypatch)
    import rd_rpc_diagnostic

    lease, result = _lease_object(), object()
    original_error = RuntimeError("PRIVATE_WORKER")
    calls = []

    def original(instance, *args, **kwargs):
        calls.append((instance, args, kwargs))
        if worker_fails:
            instance.lost.set()
            instance.cancelled.set()
            raise original_error
        return result

    def broken(*args, **kwargs):
        raise KeyboardInterrupt("PRIVATE_DIAGNOSTIC")

    monkeypatch.setattr(AttemptLease, "pulse", original)
    if diagnostic_failure == "clock":
        monkeypatch.setattr(module, "monotonic", broken)
    elif diagnostic_failure == "classifier":
        monkeypatch.setattr(rd_rpc_diagnostic, "_queue_failure", broken)
    else:
        monkeypatch.setattr(module, "print", broken, raising=False)
    caught = observed = None
    try:
        with module.diagnose_attempt_lease():
            observed = AttemptLease.pulse(lease, force=True)
    except RuntimeError as error:
        caught = error
    assert caught is (original_error if worker_fails else None)
    assert observed is (None if worker_fails else result)
    assert calls == [(lease, (), {"force": True})]
    assert AttemptLease.pulse is original


def test_lease_reports_are_bounded_and_do_not_claim_missing_renewal(
    monkeypatch, capsys
):
    import json

    from workers.research.lease import AttemptLease

    module = _lease_diagnostic(monkeypatch)
    lease = _lease_object()
    monkeypatch.setattr(module, "monotonic", lambda: 1e20)
    error = RuntimeError("PRIVATE_VALUE")

    def original(instance):
        raise error

    monkeypatch.setattr(AttemptLease, "pulse", original)
    with module.diagnose_attempt_lease():
        for _ in range(12):
            with pytest.raises(RuntimeError) as caught:
                AttemptLease.pulse(lease)
            assert caught.value is error
    output = capsys.readouterr().out
    assert output.count("RD_LEASE_PULSES_V1:") == 1 and "PRIVATE" not in output
    report = json.loads(output.removeprefix("RD_LEASE_PULSES_V1:"))
    assert len(report["failures"]) == 8 and report["failed_pulse_calls"] == 12
    assert report["successful_pulse_returns"] == 0
    assert all(row["last_success_age_ms"] is None for row in report["failures"])
    assert all(row["code"] == "unknown" for row in report["failures"])
    assert module._elapsed(0, 1e20) == module.MAXIMUM
    assert module._elapsed(10, 1) == 0


def test_lease_nonblocking_observation_never_waits_or_changes_callback(
    monkeypatch, capsys
):
    import json

    from workers.research.lease import AttemptLease

    module = _lease_diagnostic(monkeypatch)
    result, acquired = object(), []

    class BusyObserver:
        def acquire(self, *, blocking):
            acquired.append(blocking)
            assert blocking is False
            return False

    monkeypatch.setattr(module, "Lock", BusyObserver)
    monkeypatch.setattr(AttemptLease, "pulse", lambda *_: result)
    original = AttemptLease.pulse
    with module.diagnose_attempt_lease():
        assert AttemptLease.pulse(_lease_object()) is result
    assert AttemptLease.pulse is original and acquired == [False, False]
    report = json.loads(capsys.readouterr().out.removeprefix("RD_LEASE_PULSES_V1:"))
    assert report == {"status": "unavailable"}


@pytest.mark.parametrize(
    "sqlstate, expected", [("40P01", "40P01"), ("PRIVATE_STATE", "other")]
)
def test_lease_deadlock_sqlstate_is_closed(monkeypatch, sqlstate, expected):
    from sqlalchemy.exc import DBAPIError

    from tests.rdagent.test_rpc_stage_diagnostic import _diagnostic

    class DriverError(Exception):
        pass

    driver = DriverError("PRIVATE_QUERY")
    driver.sqlstate = sqlstate
    error = DBAPIError("PRIVATE_SQL", {"private": "token"}, driver, False)
    report = _diagnostic()._queue_failure(error)
    assert report["family"] == "database"
    assert report["sqlstate"] == expected
    assert "PRIVATE" not in str(report)


def test_lease_observer_scope_has_no_runtime_mutations_or_extra_threads():
    import ast

    source = (ROOT / "tests/integration/rd_lease_diagnostic.py").read_text()
    helper = ast.unparse(diagnostic_function())
    assert "observers.enter_context(diagnose_attempt_lease())" in helper
    for forbidden in (
        ".start(",
        ".join(",
        ".wait(",
        "sleep(",
        ".set(",
        "session",
        "token",
        "attempt_id",
        "worker_id",
    ):
        assert forbidden not in source
    assert "original(lease, *args, **kwargs)" in source
    assert "len(samples) < 8" in source
    assert "lock.acquire(blocking=False)" in source
    assert source.count("print(") == 1


@pytest.mark.parametrize(
    "message,expected",
    [
        ("workflow owner is busy", "workflow_owner_busy"),
        ("workflow owner scope missing", "workflow_owner_scope_missing"),
        ("private arbitrary credential error", "other"),
    ],
)
def test_lease_database_guard_is_an_exact_closed_public_literal(
    monkeypatch, message, expected
):
    from types import SimpleNamespace

    from sqlalchemy.exc import DBAPIError

    monkeypatch.syspath_prepend(str(ROOT / "tests/integration"))
    import rd_lease_diagnostic as lease
    import rd_rpc_diagnostic as rpc_diagnostic

    driver = Exception("private database detail")
    driver.sqlstate = "P0001"
    driver.diag = SimpleNamespace(message_primary=message)
    error = DBAPIError("private SQL", {"private": "parameter"}, driver)
    assert lease._database_guard(error) == expected
    assert rpc_diagnostic._queue_failure(error)["sqlstate"] == "P0001"


def test_composed_retained_dataset_qualifies_for_both_exact_readers(
    monkeypatch, tmp_path
):
    import json
    from contextlib import contextmanager
    from datetime import date
    from types import SimpleNamespace
    from uuid import uuid4

    from packages.factors.contracts import DateWindow
    from packages.structured_factor_strategy.market import ARTIFACTS, stage_market

    monkeypatch.syspath_prepend(str(ROOT / "tests/integration"))
    from rd_configured_dataset_fixture import (
        persist_configured_dataset,
        retained_marketdata_fixture,
    )

    retained = []

    @contextmanager
    def begin():
        yield SimpleNamespace(add=retained.append)

    dataset, source, _ = persist_configured_dataset(
        SimpleNamespace(begin=begin),
        uuid4(),
        SimpleNamespace(storage_root=tmp_path),
        structured=True,
    )
    assert retained == [dataset]
    root = tmp_path / dataset.storage_key
    artifacts = {name: (root / name).read_bytes() for name in ARTIFACTS}
    market = stage_market(
        (root / "manifest.json").read_bytes(),
        artifacts,
        dataset_sha256=dataset.content_sha256,
        window=DateWindow(start=date(2024, 3, 13), end=date(2024, 3, 15)),
    )
    assert market.qualification == "synthetic_by_construction_not_live_or_pit_evidence"
    assert {value.listing_id for value in market.listings} == set(
        source.universe.listing_ids
    )
    original = retained_marketdata_fixture()["bars"]
    actual = json.loads(artifacts["preview.json"])
    assert [row["close"] for row in actual] == [row["close"] for row in original]
