"""Exact historical schema capabilities, never inferred from revision ordering.

Every new revision must explicitly preserve or extend its predecessor's closure.
Known pre-backup revisions are retained solely for read compatibility checks.
"""

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class SchemaCapabilities:
    process_installation_quota: bool = False
    backup_supported: bool = False
    strategy_packages: bool = False
    note_pins: bool = False
    factor_closure_schema: str | None = None
    factor_imports: bool = False
    research_closure_schema: str | None = None
    rd_source_closure_schema: str | None = None
    native_closure_schema: str | None = None
    execution_snapshot_schema: str | None = None
    direct_analysis_schema: str | None = None
    rd_submission_schema: str | None = None
    openevolve_closure_schema: str | None = None
    structured_strategy_schema: str | None = None
    rd_seed_schema: str | None = None
    rd_steer_schema: str | None = None


SCHEMA_CAPABILITIES = MappingProxyType(
    {
        "023_rd_submissions": SchemaCapabilities(
            backup_supported=True,
            strategy_packages=True,
            note_pins=True,
            factor_closure_schema="mineagent.factor-restore-closure.v2",
            factor_imports=True,
            research_closure_schema="mineagent.research-restore-closure.v7",
            rd_source_closure_schema="mineagent.rd-source-restore-closure.v4",
            native_closure_schema="mineagent.native-hdf-restore-closure.v3",
            execution_snapshot_schema="mineagent.execution-snapshot-restore-closure.v2",
            rd_submission_schema="mineagent.rd-submission-restore-closure.v1",
            direct_analysis_schema="mineagent.direct-analysis-restore-closure.v1",
        ),
        "022_factor_analysis": SchemaCapabilities(
            backup_supported=True,
            strategy_packages=True,
            note_pins=True,
            factor_closure_schema="mineagent.factor-restore-closure.v2",
            factor_imports=True,
            research_closure_schema="mineagent.research-restore-closure.v7",
            rd_source_closure_schema="mineagent.rd-source-restore-closure.v4",
            native_closure_schema="mineagent.native-hdf-restore-closure.v3",
            execution_snapshot_schema="mineagent.execution-snapshot-restore-closure.v2",
            direct_analysis_schema="mineagent.direct-analysis-restore-closure.v1",
        ),
        "027b_provider_material": SchemaCapabilities(
            rd_seed_schema="mineagent.rd-seed-restore-closure.v1",
            rd_steer_schema="mineagent.rd-steer-closure.v1",
            process_installation_quota=True,
            backup_supported=True,
            strategy_packages=True,
            note_pins=True,
            factor_closure_schema="mineagent.factor-restore-closure.v2",
            factor_imports=True,
            research_closure_schema="mineagent.research-restore-closure.v11",
            rd_source_closure_schema="mineagent.rd-source-restore-closure.v6",
            native_closure_schema="mineagent.native-hdf-restore-closure.v3",
            execution_snapshot_schema="mineagent.execution-snapshot-restore-closure.v2",
            rd_submission_schema="mineagent.rd-submission-restore-closure.v1",
            direct_analysis_schema="mineagent.direct-analysis-restore-closure.v1",
            structured_strategy_schema="mineagent.structured-strategy-restore-closure.v1",
            openevolve_closure_schema="mineagent.openevolve-restore-closure.v1",
        ),
        "027a_contract_repairs": SchemaCapabilities(
            rd_seed_schema="mineagent.rd-seed-restore-closure.v1",
            rd_steer_schema="mineagent.rd-steer-closure.v1",
            process_installation_quota=True,
            backup_supported=True,
            strategy_packages=True,
            note_pins=True,
            factor_closure_schema="mineagent.factor-restore-closure.v2",
            factor_imports=True,
            research_closure_schema="mineagent.research-restore-closure.v11",
            rd_source_closure_schema="mineagent.rd-source-restore-closure.v6",
            native_closure_schema="mineagent.native-hdf-restore-closure.v3",
            execution_snapshot_schema="mineagent.execution-snapshot-restore-closure.v2",
            rd_submission_schema="mineagent.rd-submission-restore-closure.v1",
            direct_analysis_schema="mineagent.direct-analysis-restore-closure.v1",
            structured_strategy_schema="mineagent.structured-strategy-restore-closure.v1",
            openevolve_closure_schema="mineagent.openevolve-restore-closure.v1",
        ),
        "027_rd_controller_v3": SchemaCapabilities(
            rd_seed_schema="mineagent.rd-seed-restore-closure.v1",
            rd_steer_schema="mineagent.rd-steer-closure.v1",
            process_installation_quota=True,
            backup_supported=True,
            strategy_packages=True,
            note_pins=True,
            factor_closure_schema="mineagent.factor-restore-closure.v2",
            factor_imports=True,
            research_closure_schema="mineagent.research-restore-closure.v11",
            rd_source_closure_schema="mineagent.rd-source-restore-closure.v6",
            native_closure_schema="mineagent.native-hdf-restore-closure.v3",
            execution_snapshot_schema="mineagent.execution-snapshot-restore-closure.v2",
            rd_submission_schema="mineagent.rd-submission-restore-closure.v1",
            direct_analysis_schema="mineagent.direct-analysis-restore-closure.v1",
            structured_strategy_schema="mineagent.structured-strategy-restore-closure.v1",
            openevolve_closure_schema="mineagent.openevolve-restore-closure.v1",
        ),
        "026_rd_seeded_submissions": SchemaCapabilities(
            rd_seed_schema="mineagent.rd-seed-restore-closure.v1",
            rd_steer_schema="mineagent.rd-steer-closure.v1",
            process_installation_quota=True,
            backup_supported=True,
            strategy_packages=True,
            note_pins=True,
            factor_closure_schema="mineagent.factor-restore-closure.v2",
            factor_imports=True,
            research_closure_schema="mineagent.research-restore-closure.v10",
            rd_source_closure_schema="mineagent.rd-source-restore-closure.v5",
            native_closure_schema="mineagent.native-hdf-restore-closure.v3",
            execution_snapshot_schema="mineagent.execution-snapshot-restore-closure.v2",
            rd_submission_schema="mineagent.rd-submission-restore-closure.v1",
            direct_analysis_schema="mineagent.direct-analysis-restore-closure.v1",
            structured_strategy_schema="mineagent.structured-strategy-restore-closure.v1",
            openevolve_closure_schema="mineagent.openevolve-restore-closure.v1",
        ),
        "025_structured_factor_strategy": SchemaCapabilities(
            process_installation_quota=True,
            backup_supported=True,
            strategy_packages=True,
            note_pins=True,
            factor_closure_schema="mineagent.factor-restore-closure.v2",
            factor_imports=True,
            research_closure_schema="mineagent.research-restore-closure.v9",
            rd_source_closure_schema="mineagent.rd-source-restore-closure.v4",
            native_closure_schema="mineagent.native-hdf-restore-closure.v3",
            execution_snapshot_schema="mineagent.execution-snapshot-restore-closure.v2",
            rd_submission_schema="mineagent.rd-submission-restore-closure.v1",
            direct_analysis_schema="mineagent.direct-analysis-restore-closure.v1",
            structured_strategy_schema="mineagent.structured-strategy-restore-closure.v1",
            openevolve_closure_schema="mineagent.openevolve-restore-closure.v1",
        ),
        "024_openevolve_initial": SchemaCapabilities(
            process_installation_quota=True,
            backup_supported=True,
            strategy_packages=True,
            note_pins=True,
            factor_closure_schema="mineagent.factor-restore-closure.v2",
            factor_imports=True,
            research_closure_schema="mineagent.research-restore-closure.v8",
            rd_source_closure_schema="mineagent.rd-source-restore-closure.v4",
            native_closure_schema="mineagent.native-hdf-restore-closure.v3",
            execution_snapshot_schema="mineagent.execution-snapshot-restore-closure.v2",
            rd_submission_schema="mineagent.rd-submission-restore-closure.v1",
            direct_analysis_schema="mineagent.direct-analysis-restore-closure.v1",
            openevolve_closure_schema="mineagent.openevolve-restore-closure.v1",
        ),
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
        "021_execution_snapshots": SchemaCapabilities(
            backup_supported=True,
            strategy_packages=True,
            note_pins=True,
            factor_closure_schema="mineagent.factor-restore-closure.v2",
            factor_imports=True,
            research_closure_schema="mineagent.research-restore-closure.v6",
            rd_source_closure_schema="mineagent.rd-source-restore-closure.v3",
            native_closure_schema="mineagent.native-hdf-restore-closure.v2",
            execution_snapshot_schema="mineagent.execution-snapshot-restore-closure.v1",
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
        "020_rd_controller": SchemaCapabilities(
            backup_supported=True,
            strategy_packages=True,
            note_pins=True,
            factor_closure_schema="mineagent.factor-restore-closure.v2",
            factor_imports=True,
            research_closure_schema="mineagent.research-restore-closure.v5",
            rd_source_closure_schema="mineagent.rd-source-restore-closure.v2",
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
