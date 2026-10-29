"""Installed027 upstream hooks resolve the same frozen domain on every projection."""

from . import checkpoint
from .contracts import StructuredSearchSpec
from .observer_transport import bootstrap, canonical


def _domain():
    return StructuredSearchSpec.model_validate_json(
        canonical(bootstrap()["spec"])
    ).domain


def _normalize(metadata, programs):
    return checkpoint._normalize(metadata, programs, _domain())


def snapshot(database):
    return checkpoint.snapshot(database, _domain())


def validate_directory(path):
    return checkpoint.validate_directory(path, _domain())


state_sha256 = checkpoint.state_sha256
