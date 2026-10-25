"""Offline actual Qlib direct-analysis proof; not runtime admission or portfolio proof."""

import json
import socket
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from packages.factors.contracts import DateWindow, digest
from packages.qlib_adapter.contracts import (
    DirectProtocol,
    FactorPoint,
    LabelManifest,
    LabelRow,
)
from packages.qlib_adapter.direct import analyze_direct, analyze_label_groups


def fixture():
    sessions = tuple(date(2020, 1, 6) + timedelta(days=i) for i in range(5))
    factor_values = ((1, 2, 3), (1, 1, 1), (1, None, 3), (1, None, None))
    label_values = (
        (0.1, 0.1, 0.3),
        (0.1, 0.2, 0.3),
        (0.3, 0.2, 0.1),
        (0.2, None, None),
    )
    factors, labels = [], []
    for i, session in enumerate(sessions[:-1]):
        for j, listing in enumerate(("A", "B", "C")):
            factors.append(
                FactorPoint(
                    session=session, listing_id=listing, value=factor_values[i][j]
                )
            )
            horizon = sessions[i + 1]
            event = datetime.combine(
                horizon, datetime.min.time(), tzinfo=UTC
            ) + timedelta(hours=21)
            labels.append(
                LabelRow(
                    session=session,
                    listing_id=listing,
                    horizon_end=horizon,
                    horizon_end_time=event,
                    available_at=event,
                    value=label_values[i][j],
                )
            )
    manifest = LabelManifest(
        dataset_id=UUID(int=1),
        dataset_sha256="a" * 64,
        calendar_sha256="b" * 64,
        calendar_sessions_sha256=digest([d.isoformat() for d in sessions]),
        market_timezone="UTC",
        universe_sha256="c" * 64,
        rows_sha256=digest([r.model_dump(mode="json") for r in labels]),
        row_count=len(labels),
        horizon_sessions=1,
        price_semantics="unadjusted",
    )
    protocol = DirectProtocol(
        snapshot_id=UUID(int=2),
        snapshot_output_sha256="d" * 64,
        factor_points_sha256=digest([r.model_dump(mode="json") for r in factors]),
        label_manifest=manifest,
        window=DateWindow(start=sessions[0], end=sessions[-1]),
        listing_ids=("A", "B", "C"),
    )
    return protocol, tuple(factors), tuple(labels), sessions


def verify():
    import importlib.metadata

    assert importlib.metadata.version("pyqlib") == "0.9.8"
    attempts = []
    old = socket.socket.connect
    old_ex = socket.socket.connect_ex

    def denied(*args, **kwargs):
        attempts.append(1)
        raise RuntimeError("No connections in direct Qlib analysis")

    socket.socket.connect = socket.socket.connect_ex = denied
    try:
        protocol, factors, labels, sessions = fixture()
        result = analyze_direct(protocol, factors, labels, calendar_sessions=sessions)
        assert result.defined_days == 2 and result.total_pairs == 9
        assert [d.reason for d in result.days] == [
            "defined",
            "constant_cross_section",
            "defined",
            "insufficient_pairs",
        ]
        assert abs(result.days[0].ic - 0.8660254037844386) < 1e-12
        assert abs(result.days[0].rank_ic - 0.8660254037844386) < 1e-12
        assert (
            abs(result.days[2].ic + 1) < 1e-12
            and abs(result.days[2].rank_ic + 1) < 1e-12
        )
        assert result.days[1].ic is None and result.days[3].rank_ic is None
        try:
            analyze_direct(protocol, factors[::-1], labels, calendar_sessions=sessions)
        except ValueError:
            pass
        else:
            raise AssertionError("Reordered observations accepted")
        groups = analyze_label_groups(
            protocol, factors, labels, calendar_sessions=sessions
        )
        assert abs(groups.days[0].long_short_label_return - 0.1) < 1e-12
        assert abs(groups.days[2].long_short_label_return + 0.1) < 1e-12
        assert groups.days[1].long_short_label_return is None
        assert not attempts
        return {
            "schema": "mineagent.qlib-direct-proof.v1",
            "qlib_commit": protocol.qlib_commit,
            "socket_connect_attempts": len(attempts),
            "result": result.model_dump(mode="json"),
            "groups": groups.model_dump(mode="json"),
            "claim": "actual Qlib calc_ic only; not PG/Docker or native portfolio acceptance",
        }
    finally:
        socket.socket.connect, socket.socket.connect_ex = old, old_ex


if __name__ == "__main__":
    print(json.dumps(verify(), sort_keys=True, indent=2))
