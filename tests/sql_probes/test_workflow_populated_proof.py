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
        "        run: python -m pytest tests/sql_probes/test_workflow_populated_proof.py tests/rdagent/test_rpc_stage_diagnostic.py -q\n"
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
    target_index = named(expected, TARGET)
    expected[target_index : target_index + 1] = [
        readiness,
        expected[target_index],
        clean,
        current,
    ]
    # Full step bytes, including command, env, timeout, if and artifact policy.
    assert independent == expected
    assert len(independent) == 35
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
