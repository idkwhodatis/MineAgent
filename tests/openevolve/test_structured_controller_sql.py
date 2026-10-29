"""Literal/producer-contract checks, not PostgreSQL or physical containment proof."""

import hashlib
import json
import re

import pytest

from backend.app import oe_structured_controller_sql as module
from packages.factors.contracts import canonical_bytes
from packages.research_runtime.process_contracts import canonical, digest
from runtimes.openevolve_structured.trusted_report import report_bytes
from runtimes.openevolve_structured_host.completion_journal import (
    observed_completion,
    validate_completion,
)
from tests.openevolve.test_structured_candidate import setup_candidate
from tests.structured_controller_runtime.fixtures import records


def actual_documents(tmp_path):
    launch, identity, child, stop = records(tmp_path)
    completion = observed_completion(
        launch,
        identity,
        child,
        stop,
        reports=[
            json.loads(
                report_bytes(
                    "started", child.namespace_pid, child.controller.start_ticks
                )
            ),
            json.loads(
                report_bytes(
                    "quiescent", child.namespace_pid, child.controller.start_ticks, 0
                )
            ),
        ],
        reports_eof=True,
        daemon_exit_code=124,
        daemon_oom_killed=False,
        deadline_ns=100000000000,
        kernel_absent_ns=100000000001,
    )
    return launch, identity, child, stop, completion


def test_generated_five_payload_schemas_match_actual_typed_producer(tmp_path):
    launch, identity, child, stop, completion = actual_documents(tmp_path)
    documents = dict(
        zip(
            module.MODELS,
            (launch.intent, identity, child, stop, completion),
            strict=True,
        )
    )
    sql = module.render_upgrade_sql()
    for kind, document in documents.items():
        raw = re.search(
            r"WHEN '" + kind + r"' THEN rule := '((?:''|[^'])*)'::jsonb;", sql
        )[1]
        rule = json.loads(raw.replace("''", "'"))
        assert set(rule["properties"]) == set(document.model_dump(mode="json"))
        assert set(rule["required"]) == set(document.model_dump(mode="json"))
        assert rule["additionalProperties"] is False
    assert validate_completion(completion, launch, identity, child, stop) == completion
    assert digest(identity.model_dump(mode="json")) == child.identity_sha256
    assert completion.stop_sha256 == digest(stop.model_dump(mode="json"))


def test_reconstructed_seed_bytes_are_real_closed_json_not_lossy_jsonb():
    domain, value = setup_candidate()
    reconstructed = (
        b'{"program":'
        + canonical_bytes(domain.seed_program)
        + b',"schema_version":"mineagent.oe-structured-candidate.v1"}'
    )
    assert reconstructed == canonical_bytes(value)
    sql = module.render_upgrade_sql()
    assert (
        "mineagent_structured_raw_json(sp.config_canonical::json#>'{search,domain,seed_program}')"
        in sql
    )
    assert (
        "(intent->>'seed_size_bytes')::integer IS DISTINCT FROM octet_length(seed)"
        in sql
    )


def test_original_report_raw_newline_and_hash_bind_exact_child(tmp_path):
    _, _, child, _, completed = actual_documents(tmp_path)
    first = report_bytes("started", child.namespace_pid, child.controller.start_ticks)
    last = report_bytes(
        "quiescent", child.namespace_pid, child.controller.start_ticks, 0
    )
    assert completed.started_report.encode() == first
    assert completed.quiescent_report.encode() == last
    assert first.endswith(b"\n") and last.endswith(b"\n")
    assert hashlib.sha256(first).hexdigest() == child.started_report_sha256
    sql = module.render_upgrade_sql()
    assert "completed->>'started_report' IS DISTINCT FROM started" in sql
    assert "completed->>'quiescent_report' IS DISTINCT FROM quiescent" in sql
    assert "'child_exit_code',0,'descendants_reaped',true" in sql
    assert "true)||E'\\n'" in sql


@pytest.mark.parametrize(
    "guard",
    [
        "intent->'admission' IS DISTINCT FROM declaration",
        "intent->>'bootstrap_sha256' IS DISTINCT FROM e.bootstrap_sha256",
        "ident->>'launch_sha256' IS DISTINCT FROM launch_sha",
        "ident->>'admission_sha256' IS DISTINCT FROM admission_sha",
        "mineagent_rd_controller_kernel(ident->'leader',0)",
        "mineagent_rd_controller_kernel(child->'controller',65533)",
        "child#>'{controller,ppid}' IS DISTINCT FROM ident#>'{leader,host_pid}'",
        "child#>'{controller,pid_namespace}' IS DISTINCT FROM ident#>'{leader,pid_namespace}'",
        "child#>'{controller,cgroup}' IS DISTINCT FROM ident#>'{leader,cgroup}'",
        "child->'peer_pid' IS DISTINCT FROM child#>'{controller,host_pid}'",
        "stopped->>'container_id' IS DISTINCT FROM ident->>'container_id'",
        "completed->>'stop_sha256' IS DISTINCT FROM stop_sha",
        "completed->'reports_eof' IS DISTINCT FROM 'true'::jsonb",
        "completed->'daemon_exit_code' IS DISTINCT FROM '124'::jsonb",
        "completed->'daemon_oom_killed' IS DISTINCT FROM 'false'::jsonb",
        "(completed->>'original_deadline_monotonic_ns')::numeric+1000000000",
    ],
)
def test_same_scope_identity_report_and_completion_substitution_checks(guard):
    assert guard in module.render_upgrade_sql()


def test_containment_only_and_partial_prefixes_never_become_completed():
    sql = module.render_upgrade_sql()
    assert "IF ident IS NULL THEN RETURN; END IF;" in sql
    assert "(st.status='launch_intended' AND receipts<>1)" in sql
    assert "(st.status='stopped') IS DISTINCT FROM (stopped IS NOT NULL)" in sql
    assert "CASE WHEN child IS NULL THEN 'null'::jsonb" in sql
    assert "(completed IS NOT NULL AND (child IS NULL OR stopped IS NULL))" in sql
    completed = sql.split(
        "CREATE FUNCTION @SCHEMA@.mineagent_oes_controller_completed", 1
    )[1]
    assert "PERFORM @SCHEMA@.mineagent_oes_controller_check(epoch_id)" in completed
    assert "st.status='stopped' AND b.state='settled'" in completed
    assert "paid.outcome='conservative_unknown' AND paid.charge=b.amounts" in completed
    assert "r.kind='controller_completion'" in completed


def test_no_trigger_registration_or_recursive_terminal_call():
    sql = module.render_upgrade_sql()
    assert sql == module.render_upgrade_sql()
    assert len(sql) < 100000 and "@RULE" not in sql
    assert "CREATE TRIGGER" not in sql and "alembic_version" not in sql
    assert "mineagent_oes_epoch_check(epoch_id)" in sql
    assert "mineagent_oes_checkpoint" not in sql
    assert len(module.TABLES) == len(set(module.TABLES))
    assert module.render_downgrade_sql().count("DROP FUNCTION") == 3


@pytest.mark.parametrize(
    "field,value",
    [
        ("reports_eof", False),
        ("daemon_exit_code", 0),
        ("daemon_oom_killed", True),
        ("kernel_absent_monotonic_ns", 99999999999),
        ("kernel_absent_monotonic_ns", 101000000001),
        ("started_report", "{}\n"),
        ("stop_sha256", "0" * 64),
    ],
)
def test_actual_completion_codec_rejects_forged_observations(tmp_path, field, value):
    launch, identity, child, stop, completed = actual_documents(tmp_path)
    altered = completed.model_copy(update={field: value})
    with pytest.raises((ValueError, KeyError)):
        validate_completion(altered, launch, identity, child, stop)


def test_actual_completion_is_canonical_integer_only_receipt(tmp_path):
    *_, completed = actual_documents(tmp_path)
    raw = canonical(completed.model_dump(mode="json"))
    assert json.loads(raw)["kernel_absent_monotonic_ns"] == 100000000001
    assert b'"daemon_exit_code":124' in raw


def test_payload_size_matches_actual_nofollow_journal_reader_bound():
    assert (
        "octet_length(@SCHEMA@.mineagent_rd_canonical_json(r.payload,true)) NOT BETWEEN 1 AND 16384"
        in module.render_upgrade_sql()
    )
