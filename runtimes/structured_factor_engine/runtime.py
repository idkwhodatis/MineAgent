"""Fixed trusted structured exchange wired to unchanged real AKQuant controller.

Only the engine container invokes execute; there is no host native fallback.
"""

import hashlib

from packages.factors.contracts import canonical_bytes
from packages.structured_factor_strategy.contracts import (
    MAX_DECISIONS,
    MAX_MARKET_BYTES,
    MAX_RESULT_BYTES,
)
from packages.structured_factor_strategy.operator import ClosedExchange
from packages.structured_factor_strategy.wire import (
    StructuredResult,
    accounting_only,
    read_input,
    read_result,
)
from runtimes.portable_engine.runtime import execute, load_inputs


def execute_structured(strategy_raw, market_raw):
    inputs = read_input(strategy_raw)
    if (
        type(market_raw) is not bytes
        or not 0 < len(market_raw) <= MAX_MARKET_BYTES
        or hashlib.sha256(market_raw).hexdigest() != inputs.market_sha256
    ):
        raise ValueError("Exact structured market bytes differ")
    native = load_inputs(canonical_bytes(inputs.engine), market_raw)
    mapping = {str(b.native_listing_id) for b in inputs.listing_bindings}
    if {listing.listing_id for listing in native.listings} != mapping or not 1 <= len(
        native.sessions
    ) <= MAX_DECISIONS:
        raise ValueError("Engine universe/calendar exceeds exact structured input")
    if not any(
        s.session_date == inputs.program.signal_window.start for s in native.sessions
    ):
        raise ValueError("Declared signal day is not an actual exchange session")
    if not any(
        s.session_date >= inputs.program.signal_window.end for s in native.sessions
    ):
        raise ValueError("Real later fill/valuation session required")
    # Calendar, profile, native volume cap, cash sizing, rejected/partial fills,
    # broker accounting and native timestamps remain the existing controller's.
    exchange = ClosedExchange(inputs)
    result = execute(native, exchange)
    value = StructuredResult(
        execution_id=inputs.execution_id,
        structured_strategy_version_id=inputs.structured_strategy_version_id,
        snapshot_id=inputs.snapshot.snapshot_id,
        strategy_input_sha256=hashlib.sha256(strategy_raw).hexdigest(),
        market_sha256=inputs.market_sha256,
        decisions=tuple(exchange.records),
        accounting_tables=accounting_only(result),
    )
    raw = canonical_bytes(value)
    if len(raw) > MAX_RESULT_BYTES:
        raise ValueError("Structured result bound exceeded")
    read_result(raw, inputs)
    return raw
