"""Replace generic creator categories with adult-creator discovery interests."""

from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260828_0043"
down_revision = "20260828_0042"
branch_labels = None
depends_on = None


INTERESTS = (
    ("solo-performances", "Solo performances"),
    ("couples-collaborations", "Couples & collaborations"),
    ("glamour-lingerie", "Glamour & lingerie"),
    ("fetish-kink", "Fetish & kink"),
    ("cosplay-fantasy", "Cosplay & fantasy"),
    ("live-shows", "Live shows"),
    ("photo-sets", "Photo sets"),
    ("video-behind-scenes", "Video & behind the scenes"),
    ("audio-asmr", "Audio & ASMR"),
    ("fitness-body-confidence", "Fitness & body confidence"),
    ("roleplay-characters", "Roleplay & characters"),
    ("custom-content", "Custom content & requests"),
)

LEGACY_CATEGORY_SLUGS = (
    "collaboration",
    "design",
    "editorial",
    "fashion",
    "lifestyle",
    "live",
    "marketplace",
    "performance",
    "photography",
    "studio",
)


def _categories_table() -> sa.Table:
    return sa.table(
        "creator_categories",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("slug", sa.String()),
        sa.column("label", sa.String()),
        sa.column("enabled", sa.Boolean()),
        sa.column("position", sa.Integer()),
    )


def upgrade() -> None:
    categories = _categories_table()
    for position, (slug, label) in enumerate(INTERESTS, start=1):
        statement = postgresql.insert(categories).values(
            id=uuid4(), slug=slug, label=label, enabled=True, position=position
        )
        op.execute(
            statement.on_conflict_do_update(
                index_elements=["slug"],
                set_={"label": label, "enabled": True, "position": position},
            )
        )
    op.execute(
        sa.update(categories)
        .where(categories.c.slug.in_(LEGACY_CATEGORY_SLUGS))
        .values(enabled=False)
    )


def downgrade() -> None:
    categories = _categories_table()
    op.execute(
        sa.update(categories)
        .where(categories.c.slug.in_([slug for slug, _ in INTERESTS]))
        .values(enabled=False)
    )
    op.execute(
        sa.update(categories)
        .where(categories.c.slug.in_(LEGACY_CATEGORY_SLUGS))
        .values(enabled=True)
    )
