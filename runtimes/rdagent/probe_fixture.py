"""Synthetic standalone probe inputs; never production configuration."""

from copy import deepcopy

_FIXTURE = {
    "budget": {
        "calls": 32,
        "evaluator_executions": 24,
        "mutation_slots": 3,
        "process_slots": 4,
        "storage_bytes": 10000000,
        "tokens": 10000,
        "wall_milliseconds": 600000,
    },
    "input": {
        "calculation_universe": {
            "content_sha256": "7e341ae2c9f1b2868de5e1793d5fdae20fa14a3b080314296016c21248131ffd",
            "listing_ids": ["fixture:XNAS:AAA", "fixture:XNAS:BBB", "fixture:XNAS:CCC"],
            "membership_available_at": "2024-03-06T14:30:00Z",
            "membership_history": "synthetic_fixture",
            "membership_sha256": "5cfc6264f3dfc41fc6a97472d1fcd2009cd076225b959439eff548c24d170df9",
            "universe_version_id": None,
        },
        "calendar_sha256": "f10c1260ac049fce5d83107fe73e5f6fd06ae0daacaba4d88dfbdf3bf1e8a9f0",
        "dataset": {
            "availability_revision_sha256": "30b80064782421e52e3c87ce6a3bf45dcde200cbedc9d58940e4301e5aef038c",
            "content_sha256": "f461456001f0404025cc2f8401ce1d2d1e23221aca1365f8aad7d3920f5647b7",
            "dataset_version_id": "00000000-0000-0000-0000-000000000002",
            "input_files_sha256": "2cbfe7aff7b6ea7a0f1511eb6307a12d59f8033c9ec05801279fe47966fac1fa",
            "price_semantics": "unadjusted",
            "source_quality": "synthetic_fixture",
        },
        "input_fields": [
            {
                "dtype": "float64",
                "name": "close",
                "price_semantics": "unadjusted",
                "role": "market_input",
                "unit": "currency",
            }
        ],
        "lag_sessions": 0,
        "lookback_sessions": 2,
        "market_timezone": "America/New_York",
        "window": {"end": "2024-03-16", "start": "2024-03-06"},
    },
    "max_iterations": 3,
    "max_output_bytes_per_cutoff": 10000,
    "max_output_rows_per_cutoff": 3,
    "runtime": {
        "analysis_image": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "candidate_image": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "dependency_locks": [
            {
                "role": "analysis",
                "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            },
            {
                "role": "candidate",
                "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            },
            {
                "role": "normalizer",
                "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            },
            {
                "role": "orchestrator",
                "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            },
        ],
        "normalizer_image": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "orchestrator_image": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    },
    "task": {
        "description": "Two-session momentum",
        "factor_kind": "time_series",
        "formulation": "close / previous close - 1",
        "interpretation": "higher_preferred",
        "name": "momentum",
        "output_unit": "return",
    },
}


def research_dict():
    return deepcopy(_FIXTURE)
