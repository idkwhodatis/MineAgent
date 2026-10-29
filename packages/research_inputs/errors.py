"""Finite public derivation diagnostics: never echo arbitrary stored error text."""

import re
from typing import Literal, get_args

PublicDerivationErrorCode = Literal[
    "mapping_path_missing",
    "raw_bytes_required",
    "invalid_json_or_mapping",
    "bounded_item_list_required",
    "text_fields_required",
    "invalid_proposal_text",
    "normalized_output_limit",
    "owner_cancelled",
    "worker_interrupted",
    "derivation_failed",
    "feedback_source_blocked",
    "feedback_release_required",
    "other_derivation_error",
]
_PUBLIC = frozenset(get_args(PublicDerivationErrorCode))


def public_error_code(value: str | None) -> PublicDerivationErrorCode | None:
    if value is None:
        return None
    if type(value) is not str or re.fullmatch(r"[a-z][a-z0-9_]{0,79}", value) is None:
        raise ValueError("Unsafe derivation diagnostic")
    return value if value in _PUBLIC else "other_derivation_error"
