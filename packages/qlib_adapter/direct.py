"""Actual Qlib IC invocation over host-validated, separately bound forward labels.

No provider initialization, downloads, workspace execution or factor-code loading.
"""

import math
from datetime import date
from zoneinfo import ZoneInfo

from packages.factors.contracts import digest

from .contracts import DirectDay, DirectProtocol, DirectResult, FactorPoint, LabelRow


def analyze_direct(
    protocol: DirectProtocol,
    factors: tuple[FactorPoint, ...],
    labels: tuple[LabelRow, ...],
    *,
    calendar_sessions: tuple[date, ...],
):
    import pandas as pd
    from qlib.contrib.eva.alpha import calc_ic

    if type(protocol) is not DirectProtocol:
        raise ValueError("An explicit direct-factor protocol is required")
    if len(factors) > 1_000_000 or len(labels) > 1_000_000:
        raise ValueError("Analysis input exceeds row budget")
    if (
        len(labels) != protocol.label_manifest.row_count
        or digest([x.model_dump(mode="json") for x in labels])
        != protocol.label_manifest.rows_sha256
    ):
        raise ValueError("Forward labels differ from frozen manifest")
    if (
        tuple(sorted(set(calendar_sessions))) != calendar_sessions
        or digest([x.isoformat() for x in calendar_sessions])
        != protocol.label_manifest.calendar_sessions_sha256
    ):
        raise ValueError("Calendar sessions differ from label projection")
    session_positions = {day: i for i, day in enumerate(calendar_sessions)}
    zone = ZoneInfo(protocol.label_manifest.market_timezone)
    for row in labels:
        position = session_positions.get(row.session)
        horizon = protocol.label_manifest.horizon_sessions
        if (
            position is None
            or position + horizon >= len(calendar_sessions)
            or row.horizon_end != calendar_sessions[position + horizon]
            or row.horizon_end_time.astimezone(zone).date() != row.horizon_end
        ):
            raise ValueError("Forward label horizon does not match frozen calendar")
    keys = lambda rows: [(r.session, r.listing_id) for r in rows]
    for rows, cls in ((factors, FactorPoint), (labels, LabelRow)):
        if any(type(r) is not cls for r in rows):
            raise ValueError("Unexpected analysis row")
        if keys(rows) != sorted(set(keys(rows))):
            raise ValueError("Analysis rows must be unique and sorted")
        if any(
            r.listing_id not in protocol.listing_ids
            or not protocol.window.start <= r.session < protocol.window.end
            for r in rows
        ):
            raise ValueError("Analysis row substitutes frozen window/universe")
    if keys(factors) != keys(labels):
        raise ValueError(
            "Factor and label keys must align exactly; missing values are explicit"
        )
    if (
        digest([x.model_dump(mode="json") for x in factors])
        != protocol.factor_points_sha256
    ):
        raise ValueError("Factor projection differs from frozen binding")
    if not factors:
        raise ValueError("No factor observations")
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp(r.session), r.listing_id) for r in factors],
        names=("datetime", "instrument"),
    )
    pred = pd.Series([r.value for r in factors], index=index, dtype="float64")
    label = pd.Series([r.value for r in labels], index=index, dtype="float64")
    pairwise = pred.notna() & label.notna()
    # Filter both columns together; count missing/constant days separately below.
    ic, ric = calc_ic(pred[pairwise], label[pairwise], dropna=False)
    days = []
    for session in sorted({r.session for r in factors}):
        timestamp = pd.Timestamp(session)
        mask = pairwise & (index.get_level_values("datetime") == timestamp)
        n = int(mask.sum())
        reason = (
            "insufficient_pairs"
            if n < 2
            else "constant_cross_section"
            if pred[mask].nunique() < 2 or label[mask].nunique() < 2
            else "defined"
        )
        pearson = float(ic.get(timestamp, float("nan")))
        spearman = float(ric.get(timestamp, float("nan")))
        if reason == "defined" and not (
            math.isfinite(pearson) and math.isfinite(spearman)
        ):
            raise ValueError(
                "Qlib returned undefined correlation for a nonconstant valid cross-section"
            )
        days.append(
            DirectDay(
                session=session,
                pair_count=n,
                reason=reason,
                ic=pearson if reason == "defined" else None,
                rank_ic=spearman if reason == "defined" else None,
            )
        )
    valid = [d for d in days if d.reason == "defined"]
    return DirectResult(
        protocol_sha256=protocol.sha256,
        days=tuple(days),
        ic=math.fsum(d.ic for d in valid) / len(valid) if valid else None,
        rank_ic=math.fsum(d.rank_ic for d in valid) / len(valid) if valid else None,
        defined_days=len(valid),
        total_pairs=sum(d.pair_count for d in days),
    )


def analyze_label_groups(protocol, factors, labels, *, calendar_sessions):
    """Qlib's half top-minus-bottom label return, explicitly not a portfolio.

    Fixed half-quantiles use Qlib floor(N*q), including its documented first
    sorted-listing tie selection. No rebalancing, costs or capital are implied.
    """
    import pandas as pd
    from qlib.contrib.eva.alpha import calc_long_short_return

    from .contracts import GroupDay, GroupResult

    direct = analyze_direct(
        protocol, factors, labels, calendar_sessions=calendar_sessions
    )
    index = pd.MultiIndex.from_tuples(
        [(pd.Timestamp(r.session), r.listing_id) for r in factors],
        names=("datetime", "instrument"),
    )
    pred = pd.Series([r.value for r in factors], index=index, dtype="float64")
    label = pd.Series([r.value for r in labels], index=index, dtype="float64")
    days = []
    for day in direct.days:
        mask = (
            (index.get_level_values("datetime") == pd.Timestamp(day.session))
            & pred.notna()
            & label.notna()
        )
        reason = (
            "insufficient_pairs"
            if day.pair_count < 2
            else "constant_score"
            if pred[mask].nunique() < 2
            else "defined"
        )
        long_short = average = None
        if reason == "defined":
            ls, av = calc_long_short_return(
                pred[mask], label[mask], quantile=0.5, dropna=False
            )
            long_short, average = float(ls.iloc[0]), float(av.iloc[0])
        days.append(
            GroupDay(
                session=day.session,
                pair_count=day.pair_count,
                long_short_label_return=long_short,
                universe_label_return=average,
                reason=reason,
            )
        )
    return GroupResult(direct_protocol_sha256=protocol.sha256, days=tuple(days))
