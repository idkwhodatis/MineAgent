"""Narrow migration construction and Python reference cases, not PG execution."""

import hashlib
import json

import pytest

from backend.app.declared_upload_numeric_sql import (
    PREDECESSOR_SHA256,
    RETURN,
    SIGNED_REJECTION,
    render_downgrade_sql,
    render_upgrade_sql,
)
from packages.research_runtime.numeric_rejection import NumericRejection
from runtimes.factor_execution.protected_output import rejection_metadata


def rejection(code):
    raw = rejection_metadata("result.h5", code)
    return NumericRejection(
        schema_version="mineagent.numeric-process-rejection.v1",
        launch_sha256="a" * 64,
        identity_sha256="b" * 64,
        stop_sha256="c" * 64,
        container_id="d" * 64,
        protocol="MANU2-kind3",
        basename="result.h5",
        payload_utf8=raw.decode(),
        payload_sha256=hashlib.sha256(raw).hexdigest(),
        child_exit_code=code,
        descendants_reaped=True,
        complete_eof=True,
    )


def test_upgrade_only_inserts_one_exact_rejection_guard_and_restores_predecessor():
    old, new = render_downgrade_sql(), render_upgrade_sql()
    assert hashlib.sha256(old.encode()).hexdigest() == PREDECESSOR_SHA256
    assert new.replace(SIGNED_REJECTION + RETURN, RETURN) == old
    assert new.count("CREATE OR REPLACE FUNCTION") == 1
    assert "CREATE OR REPLACE FUNCTION @SCHEMA@.mineagent_rd_schema(" not in new
    assert "IF kind='rejection' THEN" in new
    assert "jsonb_set(v,'{child_exit_code}','0'::jsonb,false)" in new


@pytest.mark.parametrize("code", [-64, -9, -1, 0, 1, 255])
def test_actual_python_rejection_contract_accepts_exact_signed_domain(code):
    value = rejection(code)
    assert NumericRejection.model_validate_json(value.model_dump_json()) == value


@pytest.mark.parametrize("code", [-65, 256, -9.0, 0.5, "-9", True, None])
def test_python_reference_rejects_invalid_exit_code(code):
    data = rejection(-9).model_dump(mode="json")
    data["child_exit_code"] = code
    with pytest.raises(ValueError):
        NumericRejection.model_validate_json(json.dumps(data))
