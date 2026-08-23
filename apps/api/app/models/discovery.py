"""Derived discovery controls; canonical content and access stay in their domains."""

import enum
from uuid import UUID

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamped, UUIDPrimaryKey


class DiscoveryEntityType(str, enum.Enum):
    creator = "creator"
    post = "post"
    video = "video"
    gallery = "gallery"
    marketplace_listing = "marketplace_listing"
    live_room = "live_room"


class DiscoveryConfig(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "discovery_configs"
    version: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    text_weight: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    live_boost: Mapped[int] = mapped_column(Integer, default=40, nullable=False)
    recency_weight: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    engagement_weight: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    trending_window_hours: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    default_result_limit: Mapped[int] = mapped_column(Integer, default=20, nullable=False)


class DiscoveryHide(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "discovery_hides"
    __table_args__ = (UniqueConstraint("entity_type", "entity_id", name="uq_discovery_hide_entity"),)
    entity_type: Mapped[DiscoveryEntityType] = mapped_column(Enum(DiscoveryEntityType, name="discovery_entity_type"), index=True)
    entity_id: Mapped[UUID] = mapped_column(index=True)
    reason: Mapped[str] = mapped_column(String(500))
    actor_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))


class DiscoveryEvent(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "discovery_events"
    __table_args__ = (UniqueConstraint("event_type", "request_key", "entity_type", "entity_id", name="uq_discovery_event_dedupe"),)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    request_key: Mapped[str] = mapped_column(String(128), index=True)
    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    entity_type: Mapped[DiscoveryEntityType | None] = mapped_column(Enum(DiscoveryEntityType, name="discovery_entity_type", create_type=False))
    entity_id: Mapped[UUID | None] = mapped_column(index=True)
    ranking_version: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
