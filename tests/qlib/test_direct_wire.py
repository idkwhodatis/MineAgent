"""Wire validation only; no installed Qlib or process authority claim."""

import json

import pytest

from packages.factors.contracts import canonical_bytes
from packages.qlib_adapter.contracts import DirectDay, DirectResult
from packages.qlib_adapter.direct_wire import (
    DirectInput,
    read_input,
    read_result,
    write_input,
)
from runtimes.qlib.verify_direct import fixture


def inputs():
    protocol, factors, labels, calendar = fixture()
    return DirectInput(
        protocol=protocol, factors=factors, labels=labels, calendar_sessions=calendar
    )


def result(value):
    return DirectResult(
        protocol_sha256=value.protocol.sha256,
        days=(
            DirectDay(
                session=value.factors[0].session,
                pair_count=3,
                reason="defined",
                ic=0.5,
                rank_ic=0.5,
            ),
            DirectDay(
                session=value.factors[3].session,
                pair_count=3,
                reason="constant_cross_section",
                ic=None,
                rank_ic=None,
            ),
            DirectDay(
                session=value.factors[6].session,
                pair_count=2,
                reason="defined",
                ic=-1.0,
                rank_ic=-1.0,
            ),
            DirectDay(
                session=value.factors[9].session,
                pair_count=1,
                reason="insufficient_pairs",
                ic=None,
                rank_ic=None,
            ),
        ),
        ic=-0.25,
        rank_ic=-0.25,
        defined_days=2,
        total_pairs=9,
    )


def test_roundtrip_and_exact_result_semantics():
    value = inputs()
    assert read_input(write_input(value)) == value
    answer = result(value)
    assert read_result(canonical_bytes(answer), value) == answer


@pytest.mark.parametrize("field", ["factors", "labels", "calendar_sessions"])
def test_input_reordering_rejected(field):
    value = inputs().model_dump(mode="json")
    value[field].reverse()
    with pytest.raises(ValueError):
        read_input(canonical_bytes(value))


@pytest.mark.parametrize(
    "raw",
    [b"{}", b"", b" " * 16777217],
    ids=["invalid-object", "empty", "over-16-mib"],
)
def test_bounded_input(raw):
    with pytest.raises(ValueError):
        read_input(raw)


def test_duplicate_and_noncanonical_json_rejected():
    raw = write_input(inputs())
    with pytest.raises(ValueError):
        read_input(b"{" + b'"schema_version":"bad",' + raw[1:])
    with pytest.raises(ValueError):
        read_input(json.dumps(json.loads(raw)).encode())


@pytest.mark.parametrize("change", ["protocol", "session", "pairs", "missing"])
def test_result_substitution_rejected(change):
    value = inputs()
    answer = result(value).model_dump(mode="json")
    if change == "protocol":
        answer["protocol_sha256"] = "f" * 64
    elif change == "session":
        answer["days"][0]["session"] = "2019-01-01"
    elif change == "pairs":
        answer["days"][0]["pair_count"] = 4
        answer["total_pairs"] += 1
    else:
        answer["days"].pop()
        answer["total_pairs"] -= 1
    with pytest.raises(ValueError):
        read_result(canonical_bytes(answer), value)
