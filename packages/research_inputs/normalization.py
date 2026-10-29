"""Deterministic external JSON normalization; never a network/model/command tool.

A workflow host persists raw input, actual attempt status/error and these output
commitments. This function alone is not a completed workflow or trusted content.
"""

import hashlib
from dataclasses import dataclass

from packages.factors.contracts import canonical_bytes
from packages.strategy_sdk import JsonLimits, parse_json

from .contracts import ExternalFeedMapping, NormalizedExternalProposal

LIMITS = JsonLimits(
    max_bytes=65536, max_depth=16, max_nodes=50000, max_items=4096, max_string=16384
)
MAX_OUTPUT_BYTES = 262144


class NormalizationError(ValueError):
    """Safe diagnostics contain no copied external text, URLs or credentials."""

    def __init__(self, code, *, item_ordinal=None):
        self.code, self.item_ordinal = code, item_ordinal
        super().__init__(code)


@dataclass(frozen=True)
class NormalizationResult:
    raw_input_sha256: str
    mapping_sha256: str
    outputs: tuple[NormalizedExternalProposal, ...]
    output_bytes: bytes

    @property
    def output_sha256(self):
        return hashlib.sha256(self.output_bytes).hexdigest()


def _select(value, path):
    for part in path:
        if (
            type(part) is str
            and type(value) is dict
            and part in value
            or type(part) is int
            and type(value) is list
            and 0 <= part < len(value)
        ):
            value = value[part]
        else:
            raise NormalizationError("mapping_path_missing")
    return value


def _pointer(path):
    return "/" + "/".join(
        str(part).replace("~", "~0").replace("/", "~1") for part in path
    )


def normalize_external_json(raw, mapping):
    """Preserve source SHA; normalize line endings/outer whitespace, not meaning.

    Item kind is fixed by the workflow mapping, never a feed's approval/status
    fields. Text resembling commands remains quoted proposal data with no authority.
    The operation is all-or-nothing: an invalid item produces no partial outputs.
    """
    if type(raw) is not bytes:
        raise NormalizationError("raw_bytes_required")
    try:
        mapping = ExternalFeedMapping.model_validate(mapping)
        parsed = parse_json(raw, limits=LIMITS)
    except (ValueError, TypeError):
        raise NormalizationError("invalid_json_or_mapping") from None
    items = _select(parsed, mapping.items_path)
    if type(items) is not list or not 1 <= len(items) <= 32:
        raise NormalizationError("bounded_item_list_required")
    raw_sha = hashlib.sha256(raw).hexdigest()
    outputs = []
    for ordinal, item in enumerate(items):
        try:
            text = _select(item, mapping.text_path)
            title = (
                _select(item, mapping.title_path)
                if mapping.title_path is not None
                else f"External proposal {ordinal + 1}"
            )
            if type(text) is not str or type(title) is not str:
                raise NormalizationError("text_fields_required")
            output = NormalizedExternalProposal(
                raw_input_sha256=raw_sha,
                mapping_sha256=mapping.sha256,
                item_pointer=_pointer((*mapping.items_path, ordinal)),
                item_ordinal=ordinal,
                kind=mapping.kind,
                title=title.strip(),
                text=text.replace("\r\n", "\n").replace("\r", "\n").strip(),
            )
        except NormalizationError as exc:
            raise NormalizationError(exc.code, item_ordinal=ordinal) from None
        except (ValueError, TypeError, UnicodeError):
            raise NormalizationError(
                "invalid_proposal_text", item_ordinal=ordinal
            ) from None
        outputs.append(output)
    encoded = canonical_bytes([output.model_dump(mode="json") for output in outputs])
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise NormalizationError("normalized_output_limit")
    return NormalizationResult(raw_sha, mapping.sha256, tuple(outputs), encoded)
