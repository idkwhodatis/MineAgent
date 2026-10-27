"""Operational image overlay for the observer_transport public seam only.

Installed at packages/openevolve_adapter/observer_transport.py. Explicit adapter
inventory records both this mapping and its bytes; original phase6 artifact stays
unchanged. This file does not modify native sampling/evaluation/search algorithms.
"""

from runtimes.openevolve_operational.transport import (  # noqa: F401
    BOOTSTRAP,
    EVENTS,
    MAX_MESSAGE,
    SOCKET,
    TICKET,
    bootstrap,
    canonical,
    host_event,
    process_ticket,
    read_exact,
    read_record,
    unique,
)
