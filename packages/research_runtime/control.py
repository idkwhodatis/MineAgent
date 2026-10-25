"""Terminal control signals must bypass upstream ``except Exception`` retries."""

from typing import Literal

AbortCode = Literal[
    "cancelled",
    "fence_lost",
    "budget_exhausted",
    "publication_failed",
    "uncertain_transport",
    "request_drift",
    "invalid_accounting",
]
CODES = frozenset(
    {
        "cancelled",
        "fence_lost",
        "budget_exhausted",
        "publication_failed",
        "uncertain_transport",
        "request_drift",
        "invalid_accounting",
    }
)


class BrokerAbort(BaseException):
    def __init__(self, code: AbortCode):
        if code not in CODES:
            raise ValueError("Unknown broker abort code")
        self.code = code
        super().__init__(code)
