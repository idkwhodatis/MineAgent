"""Fail-closed service configuration, separate from the quant runtime."""

import re
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from backend.app.oe_structured_server_schemas import OEStructuredServerProfile
from backend.app.operations_schemas import BackupReceipt
from backend.app.rdagent_provider_profile import RDProviderServerRuntimeProfile
from backend.app.rdagent_seed_runtime_profile import RDSeededServerRuntimeProfile
from backend.app.rdagent_server_profile import RDServerRuntimeProfile
from packages.openevolve_structured.provider_operator import OEOperatorChatProfile
from packages.rdagent_provider.operator_profiles import RDOperatorModelProfile
from packages.tradegrad_adapter.model_profiles import (
    OperatorModelProfile,
    validate_registry,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None, extra="ignore", hide_input_in_errors=True
    )
    database_url: str = Field(repr=False)
    secret_key: str = Field(min_length=32, repr=False)
    storage_root: Path = Path("/var/lib/tradegrad")
    subpath: str = ""

    @field_validator("subpath")
    @classmethod
    def canonical_subpath(cls, value: str) -> str:
        if value == "/":
            return ""
        if (
            len(value) > 200
            or re.fullmatch(r"(?:/[A-Za-z0-9._-]+)*", value) is None
            or any(part in {".", ".."} for part in value.split("/"))
        ):
            raise ValueError(
                "SUBPATH must be an empty root or canonical ASCII path prefix"
            )
        return value

    access_token_minutes: int = Field(default=30, ge=1, le=1440)
    lease_seconds: int = Field(default=60, ge=10, le=3600)
    heartbeat_seconds: int = Field(default=10, ge=1, le=60)
    sse_poll_seconds: float = Field(default=1, ge=0.1, le=30)
    cors_origins: list[str] = []
    max_artifact_bytes: int = Field(default=50_000_000, ge=1024)
    data_provider_mode: str = Field(default="live", pattern=r"^(live|fixture)$")
    data_lease_seconds: int = Field(default=120, ge=30, le=600)
    data_block_days: int = Field(default=31, ge=1, le=366)
    max_active_imports_per_user: int = Field(default=4, ge=1, le=100)
    max_upload_bytes: int = Field(default=10_000_000, ge=1024, le=10_000_000)
    max_active_runs_per_user: int = Field(default=4, ge=1, le=100)

    # Sanitized JSON receipts are injected by the operator, never loaded from backup paths.
    operations_application_ref: str | None = Field(
        default=None, pattern=r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$"
    )
    operations_backup_receipts: tuple[BackupReceipt, ...] = Field(
        default=(), max_length=16
    )
    operations_receipt_max_age_seconds: int = Field(default=86400, ge=60, le=2592000)

    model_profiles_enabled: bool = False
    model_profiles: tuple[OperatorModelProfile, ...] = Field(
        default=(), max_length=64, repr=False
    )
    model_synthetic_test_mode: bool = False

    # MineAgent migrations are independently gated. These never reinterpret old
    # TradeGrad schemas/checkpoints or change the legacy execution profile.
    # Neutral primitives have no public run admission or provider transport.
    neutral_object_owner_max_bytes: int = Field(default=512 * 1024**2, ge=1024)
    neutral_object_installation_max_bytes: int = Field(default=2 * 1024**3, ge=1024)
    neutral_object_max_files: int = Field(default=10_000, ge=1, le=100_000)
    factor_library_enabled: bool = False
    factor_import_enabled: bool = False
    factor_parser_image_id: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    factor_owner_max_bytes: int = Field(default=512 * 1024 * 1024, ge=1024)
    factor_installation_max_bytes: int = Field(default=2 * 1024**3, ge=1024)
    factor_max_files: int = Field(default=10_000, ge=1, le=100_000)
    # Explicit new profile; importing configuration never enables an engine/model.
    oe_structured_research_enabled: bool = False
    oe_structured_runtime_profile: OEStructuredServerProfile | None = None
    oe_structured_provider_profiles: tuple[OEOperatorChatProfile, ...] = Field(
        default=(), max_length=64, repr=False
    )
    oe_structured_live_provider_enabled: bool = False
    oe_structured_synthetic_provider_enabled: bool = False
    oe_structured_provider_python: str | None = Field(
        default=None, pattern=r"^/[^\x00]+$", max_length=4096
    )

    rdagent_research_enabled: bool = False
    rdagent_runtime_profile: RDServerRuntimeProfile | None = None
    rdagent_seeded_runtime_profile: RDSeededServerRuntimeProfile | None = None
    rdagent_provider_runtime_profile: RDProviderServerRuntimeProfile | None = None
    rdagent_provider_profiles: tuple[RDOperatorModelProfile, ...] = Field(
        default=(), repr=False
    )
    rdagent_live_provider_enabled: bool = False
    rdagent_synthetic_provider_enabled: bool = False
    # Host-owned SDK interpreter; never selected by a public research request.
    rdagent_provider_python: Path | None = None
    openevolve_research_enabled: bool = False

    @model_validator(mode="after")
    def model_registry(self):
        validate_registry(self)
        return self

    @field_validator("database_url")
    @classmethod
    def postgres_only(cls, value: str) -> str:
        url = make_url(value)
        if url.drivername not in {"postgresql", "postgresql+psycopg"}:
            raise ValueError(
                "Only PostgreSQL with psycopg is supported; SQLite is not a substitute"
            )
        return str(
            url.set(drivername="postgresql+psycopg").render_as_string(
                hide_password=False
            )
        )

    @field_validator("secret_key")
    @classmethod
    def reject_placeholder_secret(cls, value: str) -> str:
        if value.lower() in {"changethis" * 4, "change-me" * 4}:
            raise ValueError("Use a randomly generated SECRET_KEY")
        return value

    @property
    def psycopg_url(self) -> str:
        return (
            make_url(self.database_url)
            .set(drivername="postgresql")
            .render_as_string(hide_password=False)
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
