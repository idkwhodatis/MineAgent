from contextlib import contextmanager
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, call

import pytest
from sqlalchemy.engine import make_url

from tests.sql_probes import qlib_ascii_ab as ab

_ARM_ENV = {
    "RUN_BACKUP_RESTORE_DRILL": "1",
    "DATA_PROVIDER_MODE": "fixture",
    "MODEL_PROFILES_ENABLED": "false",
    "E2E_LIVE_PROVIDERS": "0",
    "MINEAGENT_QLIB_RPC_COST": "1",
}


@pytest.mark.parametrize("arm", sorted(ab._ARMS))
def test_arm_requires_exclusive_synthetic_opt_in(arm):
    ab.validate_arm(arm, True, _ARM_ENV)
    with pytest.raises(ValueError):
        ab.validate_arm(arm, False, _ARM_ENV)
    with pytest.raises(ValueError):
        ab.validate_arm("unexpected", True, _ARM_ENV)
    for key in _ARM_ENV:
        with pytest.raises(ValueError):
            ab.validate_arm(arm, True, {**_ARM_ENV, key: ""})


def _dbs():
    return NS(
        source=make_url("postgresql+psycopg://u@127.0.0.1/db").set(
            database="tradegrad_backup_test_" + "a" * 32
        ),
        restored=make_url("postgresql+psycopg://u@127.0.0.1/db").set(
            database="tradegrad_backup_test_" + "b" * 32
        ),
    )


def test_database_guard_uses_actual_distinct_fixture_identity():
    db = _dbs()
    connection = MagicMock()
    connection.engine.url = db.source.set(port=5432)
    identity = (db.source.database, "public", "180006", True)
    connection.exec_driver_sql.return_value.one.return_value = identity
    ab._verify_database(connection, db)
    with pytest.raises(ValueError):
        ab._verify_database(connection, db, restored=True)
    for i, value in enumerate(
        ["tradegrad_backup_test_" + "0" * 32, "private", "180005", False]
    ):
        identity_variant = list(identity)
        identity_variant[i] = value
        connection.exec_driver_sql.return_value.one.return_value = identity_variant
        with pytest.raises(ValueError):
            ab._verify_database(connection, db)
    with pytest.raises(ValueError):
        ab._verify_database(connection, NS(source=db.source, restored=db.source))
    with pytest.raises(ValueError):
        ab._verify_database(
            connection, NS(source=db.source, restored=db.restored.set(host="other"))
        )


@pytest.mark.parametrize("arm", sorted(ab._ARMS))
def test_only_treatment_writes_exact_sql_and_attests_committed_body(monkeypatch, arm):
    first, committed = MagicMock(), MagicMock()

    @contextmanager
    def begin():
        yield first

    @contextmanager
    def connect():
        yield committed

    verify, attest = MagicMock(), MagicMock()
    monkeypatch.setattr(ab, "_verify_database", verify)
    monkeypatch.setattr(ab, "_attest", attest)
    monkeypatch.setattr(ab, "_catalog_identity", lambda conn: ["enc", "fn"])
    dbs = _dbs()
    phases = []
    ab.prepare_arm(NS(begin=begin, connect=connect), dbs, arm, mark_phase=phases.append)
    assert phases == [
        "database_identity",
        "pinned_baseline",
        *(["graph_body_edit"] if arm == "ascii_reuse" else []),
        "source_attestation",
    ]
    assert verify.call_args_list == [call(first, dbs), call(committed, dbs)]
    assert attest.call_args_list == [
        call(first, arm="baseline"),
        *([call(first, arm=arm)] if arm == "ascii_reuse" else []),
        call(committed, arm=arm),
    ]
    committed.exec_driver_sql.assert_not_called()
    if arm == "baseline":
        first.exec_driver_sql.assert_not_called()
    else:
        first.exec_driver_sql.assert_called_once_with(
            ab.probe.candidate_source().replace("@SCHEMA@", '"public"')
        )


def test_attest_checks_both_source_and_cold_restore_without_writes(monkeypatch):
    source, target = MagicMock(), MagicMock()
    se, re = MagicMock(), MagicMock()
    se.connect.return_value.__enter__.return_value = source
    re.connect.return_value.__enter__.return_value = target
    verify, attest = MagicMock(), MagicMock()
    monkeypatch.setattr(ab, "_verify_database", verify)
    monkeypatch.setattr(ab, "_attest", attest)
    dbs = _dbs()
    ab.attest_restored(se, re, dbs, "ascii_reuse")
    assert verify.call_args_list == [
        call(source, dbs),
        call(target, dbs, restored=True),
    ]
    assert attest.call_args_list == [
        call(source, arm="ascii_reuse"),
        call(target, arm="ascii_reuse"),
    ]
    source.exec_driver_sql.assert_not_called()
    target.exec_driver_sql.assert_not_called()
