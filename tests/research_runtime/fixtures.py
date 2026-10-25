"""Explicit immutable legacy test records, never production defaults."""

from uuid import uuid4


def seed_config(**changes):
    config = {
        "task": "cn_time",
        "dataset_version_id": str(uuid4()),
        "strategy_version_id": str(uuid4()),
        "start_date": "2024-01-01",
        "end_date": "2024-02-01",
        "profile": "corrected_explicit_config",
        "market": "CN",
        "dataset_sha256": "a" * 64,
        "strategy_sha256": "b" * 64,
        "execution_mode": "seed_evaluation",
        "is_synthetic": True,
    }
    config.update(changes)
    return config
