"""Exact historical schema capabilities, never inferred from revision ordering.

Every new revision must explicitly preserve or extend its predecessor's closure.
Known pre-backup revisions are retained solely for read compatibility checks.
"""

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class SchemaCapabilities:
    backup_supported: bool = False
    strategy_packages: bool = False
    note_pins: bool = False
    factor_closure_schema: str | None = None
    factor_imports: bool = False
    research_closure_schema: str | None = None
    rd_source_closure_schema: str | None = None
    native_closure_schema: str | None = None


SCHEMA_CAPABILITIES = MappingProxyType(
    {
        "001_m1": SchemaCapabilities(),
        "002_m2": SchemaCapabilities(),
        "003_m3": SchemaCapabilities(backup_supported=True),
        "004_m5": SchemaCapabilities(backup_supported=True, strategy_packages=True),
        "005_settings": SchemaCapabilities(
            backup_supported=True, strategy_packages=True
        ),
        "006_provider_connections": SchemaCapabilities(
            backup_supported=True, strategy_packages=True
        ),
        "007_research_specs": SchemaCapabilities(
            backup_supported=True, strategy_packages=True
        ),
        "008_comparisons": SchemaCapabilities(
            backup_supported=True, strategy_packages=True
        ),
        "009_independent_evaluations": SchemaCapabilities(
            backup_supported=True, strategy_packages=True
        ),
        "010_research_notes": SchemaCapabilities(
            backup_supported=True, strategy_packages=True, note_pins=True
        ),
        "011_note_pin_trigger": SchemaCapabilities(
            backup_supported=True, strategy_packages=True, note_pins=True
        ),
        "012_factor_library": SchemaCapabilities(
            backup_supported=True,
            strategy_packages=True,
            note_pins=True,
            factor_closure_schema="mineagent.factor-restore-closure.v1",
        ),
        "013_factor_import": SchemaCapabilities(
            backup_supported=True,
            strategy_packages=True,
            note_pins=True,
            factor_closure_schema="mineagent.factor-restore-closure.v2",
            factor_imports=True,
        ),
        "014_run_identity": SchemaCapabilities(
            backup_supported=True,
            strategy_packages=True,
            note_pins=True,
            factor_closure_schema="mineagent.factor-restore-closure.v2",
            factor_imports=True,
        ),
        "015_neutral_primitives": SchemaCapabilities(
            backup_supported=True,
            strategy_packages=True,
            note_pins=True,
            factor_closure_schema="mineagent.factor-restore-closure.v2",
            factor_imports=True,
            research_closure_schema="mineagent.research-restore-closure.v1",
        ),
        "017_numeric_process_boundary": SchemaCapabilities(
            backup_supported=True,
            strategy_packages=True,
            note_pins=True,
            factor_closure_schema="mineagent.factor-restore-closure.v2",
            factor_imports=True,
            research_closure_schema="mineagent.research-restore-closure.v3",
        ),
        "016_process_versioning": SchemaCapabilities(
            backup_supported=True,
            strategy_packages=True,
            note_pins=True,
            factor_closure_schema="mineagent.factor-restore-closure.v2",
            factor_imports=True,
            research_closure_schema="mineagent.research-restore-closure.v2",
        ),
        "018_rd_source_persistence": SchemaCapabilities(
            backup_supported=True,
            strategy_packages=True,
            note_pins=True,
            factor_closure_schema="mineagent.factor-restore-closure.v2",
            factor_imports=True,
            research_closure_schema="mineagent.research-restore-closure.v4",
            rd_source_closure_schema="mineagent.rd-source-restore-closure.v1",
        ),
        "019_native_hdf_storage": SchemaCapabilities(
            backup_supported=True,
            strategy_packages=True,
            note_pins=True,
            factor_closure_schema="mineagent.factor-restore-closure.v2",
            factor_imports=True,
            research_closure_schema="mineagent.research-restore-closure.v4",
            rd_source_closure_schema="mineagent.rd-source-restore-closure.v1",
            native_closure_schema="mineagent.native-hdf-restore-closure.v1",
        ),
    }
)
BACKUP_REVISIONS = frozenset(
    revision
    for revision, capabilities in SCHEMA_CAPABILITIES.items()
    if capabilities.backup_supported
)


def capabilities_for(revision: str) -> SchemaCapabilities:
    if type(revision) is not str or revision not in SCHEMA_CAPABILITIES:
        raise ValueError("Unsupported application schema revision")
    return SCHEMA_CAPABILITIES[revision]
