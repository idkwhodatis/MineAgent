"""Explicit026 provider dispatch layered over unchanged024 protocols."""

from packages.openevolve_adapter import persisted_broker as prior
from packages.rdagent_provider import codec
from packages.rdagent_provider.accounting import (
    request_reservation as provider_reservation,
)


def parse_request(protocol, value):
    return (
        codec.parse_request(protocol, value)
        if protocol in codec.PROTOCOLS
        else prior.parse_request(protocol, value)
    )


def validate_response(protocol, request, response):
    return (
        codec.validate_response(protocol, request, response)
        if protocol in codec.PROTOCOLS
        else prior.validate_response(protocol, request, response)
    )


def request_reservation(protocol, value, *, binding=None):
    if protocol not in codec.PROTOCOLS:
        return prior.request_reservation(protocol, value)
    if binding is None:
        raise ValueError(
            "Provider reservation requires exact persisted run model binding"
        )
    return provider_reservation(codec.parse_request(protocol, value), binding)


validate_context = prior.validate_context
