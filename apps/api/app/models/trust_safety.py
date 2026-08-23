"""Central, append-oriented Trust & Safety case-management records."""

import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamped, UUIDPrimaryKey


class ReportTargetType(str, enum.Enum):
    user = "user"
    creator = "creator"
    post = "post"
    comment = "comment"
    media = "media"
    message = "message"
    live_room = "live_room"
    live_chat_message = "live_chat_message"
    marketplace_listing = "marketplace_listing"
    marketplace_order = "marketplace_order"
    featured_placement = "featured_placement"
    referral_affiliate = "referral_affiliate"


class ReportReason(str, enum.Enum):
    harassment = "harassment"
    spam = "spam"
    impersonation = "impersonation"
    non_consensual_content = "non_consensual_content"
    underage_concern = "underage_concern"
    illegal_content = "illegal_content"
    copyright = "copyright"
    scam_fraud = "scam_fraud"
    prohibited_marketplace_item = "prohibited_marketplace_item"
    threat_abuse = "threat_abuse"
    privacy = "privacy"
    other = "other"


class ModerationCaseStatus(str, enum.Enum):
    open = "open"
    triage = "triage"
    investigating = "investigating"
    action_required = "action_required"
    resolved = "resolved"
    dismissed = "dismissed"
    appealed = "appealed"
    reopened = "reopened"


class ModerationSeverity(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class ModerationQueue(str, enum.Enum):
    general = "general"
    content = "content"
    live = "live"
    marketplace = "marketplace"
    consent = "consent"
    fraud = "fraud"
    appeals = "appeals"
    urgent = "urgent"


class ModerationActionType(str, enum.Enum):
    content_hide = "content_hide"
    content_remove = "content_remove"
    content_restore = "content_restore"
    creator_discovery_hide = "creator_discovery_hide"
    creator_suspend = "creator_suspend"
    creator_unsuspend = "creator_unsuspend"
    user_suspend = "user_suspend"
    marketplace_listing_remove = "marketplace_listing_remove"
    marketplace_selling_suspend = "marketplace_selling_suspend"
    live_terminate = "live_terminate"
    featured_placement_disable = "featured_placement_disable"
    referral_affiliate_suspend = "referral_affiliate_suspend"
    temporary_containment = "temporary_containment"
    warning = "warning"
    no_action = "no_action"


class AppealStatus(str, enum.Enum):
    submitted = "submitted"
    under_review = "under_review"
    upheld = "upheld"
    overturned = "overturned"
    partially_overturned = "partially_overturned"
    withdrawn = "withdrawn"
    expired = "expired"


class ConsentReleaseType(str, enum.Enum):
    content_participation = "content_participation"
    model_release = "model_release"
    co_performer_release = "co_performer_release"
    live_participation = "live_participation"


class ConsentReleaseStatus(str, enum.Enum):
    draft = "draft"
    pending = "pending"
    verified = "verified"
    rejected = "rejected"
    expired = "expired"
    revoked = "revoked"
    superseded = "superseded"


class TrustSafetyReport(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "trust_safety_reports"
    __table_args__ = (
        Index(
            "ix_ts_report_target_reason_created", "target_type", "target_id", "reason", "created_at"
        ),
    )
    reporter_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    target_type: Mapped[ReportTargetType] = mapped_column(
        Enum(ReportTargetType, name="ts_report_target_type"), index=True
    )
    target_id: Mapped[UUID] = mapped_column(index=True)
    reason: Mapped[ReportReason] = mapped_column(
        Enum(ReportReason, name="ts_report_reason"), index=True
    )
    details: Mapped[str | None] = mapped_column(Text)
    context_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    case_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("moderation_cases.id", ondelete="RESTRICT"), index=True
    )
    duplicate_of_report_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("trust_safety_reports.id", ondelete="RESTRICT")
    )


class ModerationCase(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "moderation_cases"
    __table_args__ = (
        Index(
            "ix_moderation_cases_queue_status_priority", "queue", "status", "priority", "created_at"
        ),
    )
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    primary_target_type: Mapped[ReportTargetType] = mapped_column(
        Enum(ReportTargetType, name="ts_report_target_type", create_type=False), index=True
    )
    primary_target_id: Mapped[UUID] = mapped_column(index=True)
    status: Mapped[ModerationCaseStatus] = mapped_column(
        Enum(ModerationCaseStatus, name="moderation_case_status"),
        default=ModerationCaseStatus.open,
        index=True,
    )
    severity: Mapped[ModerationSeverity] = mapped_column(
        Enum(ModerationSeverity, name="moderation_severity"),
        default=ModerationSeverity.medium,
        index=True,
    )
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
    queue: Mapped[ModerationQueue] = mapped_column(
        Enum(ModerationQueue, name="moderation_queue"), default=ModerationQueue.general, index=True
    )
    assigned_moderator_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_summary: Mapped[str | None] = mapped_column(String(500))


class ModerationEvidence(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "moderation_evidence"
    case_id: Mapped[UUID] = mapped_column(
        ForeignKey("moderation_cases.id", ondelete="RESTRICT"), index=True
    )
    source_type: Mapped[str] = mapped_column(String(64))
    source_id: Mapped[UUID | None] = mapped_column(index=True)
    snapshot: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    safe_reference: Mapped[str | None] = mapped_column(String(512))
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class ModerationCaseNote(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "moderation_case_notes"
    case_id: Mapped[UUID] = mapped_column(
        ForeignKey("moderation_cases.id", ondelete="RESTRICT"), index=True
    )
    author_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    body: Mapped[str] = mapped_column(Text)


class ModerationAction(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "moderation_actions"
    __table_args__ = (
        UniqueConstraint(
            "case_id", "action_type", "target_type", "target_id", name="uq_moderation_action_replay"
        ),
    )
    case_id: Mapped[UUID] = mapped_column(
        ForeignKey("moderation_cases.id", ondelete="RESTRICT"), index=True
    )
    action_type: Mapped[ModerationActionType] = mapped_column(
        Enum(ModerationActionType, name="moderation_action_type"), index=True
    )
    target_type: Mapped[ReportTargetType] = mapped_column(
        Enum(ReportTargetType, name="ts_report_target_type", create_type=False), index=True
    )
    target_id: Mapped[UUID] = mapped_column(index=True)
    actor_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    reason: Mapped[str] = mapped_column(String(500))
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reversal_action_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("moderation_actions.id", ondelete="RESTRICT")
    )


class ModerationAppeal(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "moderation_appeals"
    __table_args__ = (
        UniqueConstraint(
            "moderation_action_id", "status", name="uq_moderation_appeal_active_status"
        ),
    )
    moderation_case_id: Mapped[UUID] = mapped_column(
        ForeignKey("moderation_cases.id", ondelete="RESTRICT"), index=True
    )
    moderation_action_id: Mapped[UUID] = mapped_column(
        ForeignKey("moderation_actions.id", ondelete="RESTRICT"), index=True
    )
    appellant_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[AppealStatus] = mapped_column(
        Enum(AppealStatus, name="appeal_status"), default=AppealStatus.submitted, index=True
    )
    policy_deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewer_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    outcome: Mapped[str | None] = mapped_column(String(500))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exceptional_late_review_reason: Mapped[str | None] = mapped_column(String(500))


class ConsentRelease(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "consent_releases"
    owner_creator_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="RESTRICT"), index=True
    )
    release_type: Mapped[ConsentReleaseType] = mapped_column(
        Enum(ConsentReleaseType, name="consent_release_type"), index=True
    )
    status: Mapped[ConsentReleaseStatus] = mapped_column(
        Enum(ConsentReleaseStatus, name="consent_release_status"),
        default=ConsentReleaseStatus.draft,
        index=True,
    )
    participant_reference: Mapped[str] = mapped_column(String(255))
    scope_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    evidence_reference: Mapped[str | None] = mapped_column(String(512))
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    supersedes_release_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("consent_releases.id", ondelete="RESTRICT")
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )


class ConsentReleaseContent(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "consent_release_contents"
    __table_args__ = (
        UniqueConstraint("consent_release_id", "content_id", name="uq_consent_release_content"),
    )
    consent_release_id: Mapped[UUID] = mapped_column(
        ForeignKey("consent_releases.id", ondelete="RESTRICT"), index=True
    )
    content_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_items.id", ondelete="RESTRICT"), index=True
    )
    requires_consent: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
