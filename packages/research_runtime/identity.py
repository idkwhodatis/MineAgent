"""Closed legacy identities. Unknown configurations never become seed runs.

This module classifies persisted identities; each versioned family's existing
validator still validates its complete payload. No input is rewritten here.
"""

import re
from datetime import date
from uuid import UUID

LEGACY_PURPOSE = "legacy_strategy"
LEGACY_ENGINE = "legacy"
LEGACY_SCHEMAS = frozenset(
    {
        "tradegrad.run.v1",
        "tradegrad.portable.run.v1",
        "tradegrad.final-evaluation.v1",
        "tradegrad.portable.final-evaluation.v1",
        "tradegrad.independent-evaluation.v1",
        "tradegrad.portable.independent-evaluation.v1",
        "tradegrad.comparison.v1",
    }
)
SEED_KEYS = frozenset(
    {
        "task",
        "dataset_version_id",
        "strategy_version_id",
        "start_date",
        "end_date",
        "profile",
        "market",
        "dataset_sha256",
        "strategy_sha256",
        "execution_mode",
        "is_synthetic",
    }
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class RunIdentityError(ValueError):
    """An unsupported or malformed immutable run identity."""


def _invalid():
    return RunIdentityError("Unsupported immutable run identity")


def validate_seed_config(config: dict) -> None:
    """Validate the exact enriched RunCreate stored by legacy submit_run."""
    if type(config) is not dict or set(config) != SEED_KEYS:
        raise _invalid()
    if (
        config["task"] not in ("cn_time", "us_time", "cn_cross", "us_cross")
        or config["profile"] != "corrected_explicit_config"
        or config["execution_mode"] != "seed_evaluation"
        or config["market"] != config["task"][:2].upper()
        or type(config["is_synthetic"]) is not bool
    ):
        raise _invalid()
    for key in ("dataset_sha256", "strategy_sha256"):
        if type(config[key]) is not str or not _SHA256.fullmatch(config[key]):
            raise _invalid()
    for key in ("dataset_version_id", "strategy_version_id"):
        try:
            value = config[key]
            if type(value) is not str or str(UUID(value)) != value:
                raise _invalid()
        except (ValueError, TypeError, AttributeError):
            raise _invalid() from None
    parsed = []
    for key in ("start_date", "end_date"):
        try:
            value = config[key]
            if type(value) is not str:
                raise _invalid()
            day = date.fromisoformat(value)
            if day.isoformat() != value:
                raise _invalid()
            parsed.append(day)
        except (ValueError, TypeError):
            raise _invalid() from None
    if parsed[0] >= parsed[1]:
        raise _invalid()


def classify_legacy_config(config: dict) -> str | None:
    """Return exact schema, or None only for the valid unversioned seed format."""
    if type(config) is not dict:
        raise _invalid()
    if "schema_version" in config:
        schema = config["schema_version"]
        if type(schema) is not str or schema not in LEGACY_SCHEMAS:
            raise _invalid()
        return schema
    validate_seed_config(config)
    return None


def require_legacy_identity(run) -> str | None:
    """Reject absent discriminators rather than guessing a legacy identity."""
    if (
        getattr(run, "purpose", None) != LEGACY_PURPOSE
        or getattr(run, "engine", None) != LEGACY_ENGINE
    ):
        raise _invalid()
    return classify_legacy_config(getattr(run, "config", None))
