"""Independent, owner-bound factor evidence. Never backed by a strategy/run."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models import Base, utcnow


class OwnedEvidence:
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


def owner_key():
    return UniqueConstraint("owner_id", "id")


def owned_fk(column, table):
    return ForeignKeyConstraint(
        ["owner_id", column], [f"{table}.owner_id", f"{table}.id"]
    )


class Factor(OwnedEvidence, Base):
    __tablename__ = "factors"
    name: Mapped[str] = mapped_column(String(240))
    category: Mapped[str | None] = mapped_column(String(120))
    bookmarked: Mapped[bool] = mapped_column(Boolean, default=False)
    presentation_revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (owner_key(), CheckConstraint("presentation_revision > 0"))


class FactorObject(OwnedEvidence, Base):
    __tablename__ = "factor_objects"
    storage_key: Mapped[str] = mapped_column(String(300))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    media_type: Mapped[str] = mapped_column(String(100))
    object_kind: Mapped[str] = mapped_column(String(40))
    __table_args__ = (
        owner_key(),
        UniqueConstraint("owner_id", "storage_key"),
        UniqueConstraint("owner_id", "id", "sha256"),
        CheckConstraint("size_bytes >= 0"),
        CheckConstraint(
            "media_type = CASE object_kind WHEN 'code' THEN 'text/plain' WHEN 'values_partition' THEN 'application/vnd.apache.parquet' WHEN 'package' THEN 'application/zip' ELSE 'application/json' END"
        ),
        CheckConstraint(
            "storage_key = 'factors/' || owner_id::text || '/objects/' || sha256 || CASE object_kind WHEN 'code' THEN '.py' WHEN 'values_partition' THEN '.parquet' WHEN 'package' THEN '.zip' ELSE '.json' END"
        ),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'"),
        CheckConstraint(
            "object_kind IN ('code','snapshot_manifest','values_partition','calculation_samples','analysis_manifest','analysis_series','native_evidence','package')"
        ),
    )


class FactorVersion(OwnedEvidence, Base):
    __tablename__ = "factor_versions"
    factor_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    ordinal: Mapped[int] = mapped_column(Integer)
    parent_version_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB)
    definition_sha256: Mapped[str] = mapped_column(String(64))
    code: Mapped[str] = mapped_column(Text)
    code_sha256: Mapped[str] = mapped_column(String(64))
    code_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    source_kind: Mapped[str] = mapped_column(String(40))
    source_event_key: Mapped[str | None] = mapped_column(String(128))
    source_provenance: Mapped[dict[str, Any]] = mapped_column(JSONB)
    __table_args__ = (
        owner_key(),
        owned_fk("factor_id", "factors"),
        owned_fk("parent_version_id", "factor_versions"),
        ForeignKeyConstraint(
            ["owner_id", "code_object_id", "code_sha256"],
            ["factor_objects.owner_id", "factor_objects.id", "factor_objects.sha256"],
        ),
        UniqueConstraint("factor_id", "ordinal"),
        UniqueConstraint("owner_id", "source_kind", "source_event_key"),
        UniqueConstraint("owner_id", "id", "definition_sha256"),
        CheckConstraint("ordinal > 0"),
        CheckConstraint("octet_length(code) <= 131072"),
        CheckConstraint(
            "definition_sha256 ~ '^[0-9a-f]{64}$' AND code_sha256 ~ '^[0-9a-f]{64}$'"
        ),
        CheckConstraint("definition->>'code_sha256' = code_sha256"),
    )


class FeatureSnapshot(OwnedEvidence, Base):
    __tablename__ = "feature_snapshots"
    factor_version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    dataset_version_id: Mapped[UUID] = mapped_column(ForeignKey("dataset_versions.id"))
    dataset_content_sha256: Mapped[str] = mapped_column(String(64))
    definition_sha256: Mapped[str] = mapped_column(String(64))
    input_digest: Mapped[str] = mapped_column(String(64))
    output_digest: Mapped[str] = mapped_column(String(64))
    manifest_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB)
    environment_digest: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int] = mapped_column(BigInteger)
    valid_count: Mapped[int] = mapped_column(BigInteger)
    missing_count: Mapped[int] = mapped_column(BigInteger)
    identity_mode: Mapped[str] = mapped_column(String(30), default="local")
    local_verification: Mapped[dict[str, Any]] = mapped_column(JSONB)
    source_claims: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    __table_args__ = (
        owner_key(),
        ForeignKeyConstraint(
            ["owner_id", "factor_version_id", "definition_sha256"],
            [
                "factor_versions.owner_id",
                "factor_versions.id",
                "factor_versions.definition_sha256",
            ],
        ),
        ForeignKeyConstraint(
            ["owner_id", "manifest_object_id", "manifest_sha256"],
            ["factor_objects.owner_id", "factor_objects.id", "factor_objects.sha256"],
        ),
        UniqueConstraint("owner_id", "id", "factor_version_id"),
        UniqueConstraint("owner_id", "input_digest", "output_digest"),
        CheckConstraint(
            "row_count = valid_count + missing_count AND valid_count >= 0 AND missing_count >= 0"
        ),
        CheckConstraint("identity_mode = 'local'"),
    )


class FeatureSnapshotObject(OwnedEvidence, Base):
    __tablename__ = "feature_snapshot_objects"
    snapshot_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    role: Mapped[str] = mapped_column(String(40))
    ordinal: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        owner_key(),
        owned_fk("snapshot_id", "feature_snapshots"),
        owned_fk("object_id", "factor_objects"),
        UniqueConstraint("snapshot_id", "role", "ordinal"),
        UniqueConstraint("snapshot_id", "object_id"),
        CheckConstraint("ordinal >= 0"),
    )


class FactorEvaluation(OwnedEvidence, Base):
    __tablename__ = "factor_evaluations"
    factor_version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    feature_snapshot_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(40))
    protocol: Mapped[dict[str, Any]] = mapped_column(JSONB)
    protocol_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    result_payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result_sha256: Mapped[str] = mapped_column(String(64))
    environment_digest: Mapped[str] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(80))
    __table_args__ = (
        owner_key(),
        owned_fk("factor_version_id", "factor_versions"),
        ForeignKeyConstraint(
            ["owner_id", "feature_snapshot_id", "factor_version_id"],
            [
                "feature_snapshots.owner_id",
                "feature_snapshots.id",
                "feature_snapshots.factor_version_id",
            ],
        ),
        CheckConstraint(
            "kind IN ('materialization','direct_factor_analysis','native_prediction_analysis','native_portfolio_analysis')"
        ),
        CheckConstraint("status IN ('succeeded','failed','cancelled')"),
        CheckConstraint("status <> 'succeeded' OR feature_snapshot_id IS NOT NULL"),
    )


class FactorBundle(OwnedEvidence, Base):
    __tablename__ = "factor_bundles"
    name: Mapped[str] = mapped_column(String(240))
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB)
    content_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (owner_key(),)


class FactorBundleMember(OwnedEvidence, Base):
    __tablename__ = "factor_bundle_members"
    bundle_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    factor_version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    definition_sha256: Mapped[str] = mapped_column(String(64))
    ordinal: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        owner_key(),
        owned_fk("bundle_id", "factor_bundles"),
        ForeignKeyConstraint(
            ["owner_id", "factor_version_id", "definition_sha256"],
            [
                "factor_versions.owner_id",
                "factor_versions.id",
                "factor_versions.definition_sha256",
            ],
        ),
        UniqueConstraint("bundle_id", "factor_version_id"),
        UniqueConstraint("bundle_id", "ordinal"),
        CheckConstraint("ordinal >= 0"),
    )


class StrategyFactorDependency(OwnedEvidence, Base):
    __tablename__ = "strategy_factor_dependencies"
    strategy_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategy_versions.id")
    )
    factor_bundle_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    dependency_sha256: Mapped[str] = mapped_column(String(64))
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB)
    __table_args__ = (
        owner_key(),
        owned_fk("factor_bundle_id", "factor_bundles"),
        UniqueConstraint("owner_id", "strategy_version_id"),
    )


class FactorSubmission(OwnedEvidence, Base):
    __tablename__ = "factor_submissions"
    operation: Mapped[str] = mapped_column(String(80))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_sha256: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    __table_args__ = (
        owner_key(),
        UniqueConstraint("owner_id", "operation", "idempotency_key"),
        CheckConstraint("idempotency_key ~ '^[!-~]{1,128}$'"),
    )


class FactorComparison(OwnedEvidence, Base):
    __tablename__ = "factor_comparisons"
    left_version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    right_version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB)
    content_sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        owner_key(),
        owned_fk("left_version_id", "factor_versions"),
        owned_fk("right_version_id", "factor_versions"),
    )


class FactorPackage(OwnedEvidence, Base):
    __tablename__ = "factor_packages"
    factor_version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    archive_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    archive_sha256: Mapped[str] = mapped_column(String(64))
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB)
    __table_args__ = (
        owner_key(),
        owned_fk("factor_version_id", "factor_versions"),
        ForeignKeyConstraint(
            ["owner_id", "archive_object_id", "archive_sha256"],
            ["factor_objects.owner_id", "factor_objects.id", "factor_objects.sha256"],
        ),
    )


FACTOR_TABLES = tuple(
    cls.__table__
    for cls in (
        Factor,
        FactorObject,
        FactorVersion,
        FeatureSnapshot,
        FeatureSnapshotObject,
        FactorEvaluation,
        FactorBundle,
        FactorBundleMember,
        StrategyFactorDependency,
        FactorSubmission,
        FactorComparison,
        FactorPackage,
    )
)
