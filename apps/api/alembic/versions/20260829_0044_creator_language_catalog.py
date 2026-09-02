"""Seed a broad, platform-approved creator language catalogue.

Revision ID: 20260829_0044
Revises: 20260828_0043
Create Date: 2026-08-29
"""

from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260829_0044"
down_revision = "20260828_0043"
branch_labels = None
depends_on = None


LANGUAGES = (
    ("af", "Afrikaans"),
    ("am", "Amharic"),
    ("ar", "Arabic"),
    ("az", "Azerbaijani"),
    ("be", "Belarusian"),
    ("bg", "Bulgarian"),
    ("bn", "Bengali"),
    ("bs", "Bosnian"),
    ("ca", "Catalan"),
    ("cs", "Czech"),
    ("cy", "Welsh"),
    ("da", "Danish"),
    ("de", "German"),
    ("el", "Greek"),
    ("en", "English"),
    ("es", "Spanish"),
    ("et", "Estonian"),
    ("eu", "Basque"),
    ("fa", "Persian"),
    ("fi", "Finnish"),
    ("fil", "Filipino"),
    ("fr", "French"),
    ("ga", "Irish"),
    ("gu", "Gujarati"),
    ("he", "Hebrew"),
    ("hi", "Hindi"),
    ("hr", "Croatian"),
    ("hu", "Hungarian"),
    ("hy", "Armenian"),
    ("id", "Indonesian"),
    ("is", "Icelandic"),
    ("it", "Italian"),
    ("ja", "Japanese"),
    ("ka", "Georgian"),
    ("km", "Khmer"),
    ("kn", "Kannada"),
    ("ko", "Korean"),
    ("lo", "Lao"),
    ("lt", "Lithuanian"),
    ("lv", "Latvian"),
    ("mk", "Macedonian"),
    ("ml", "Malayalam"),
    ("mr", "Marathi"),
    ("ms", "Malay"),
    ("my", "Burmese"),
    ("ne", "Nepali"),
    ("nl", "Dutch"),
    ("no", "Norwegian"),
    ("pa", "Punjabi"),
    ("pl", "Polish"),
    ("pt", "Portuguese"),
    ("ro", "Romanian"),
    ("ru", "Russian"),
    ("si", "Sinhala"),
    ("sk", "Slovak"),
    ("sl", "Slovenian"),
    ("so", "Somali"),
    ("sq", "Albanian"),
    ("sr", "Serbian"),
    ("sv", "Swedish"),
    ("sw", "Swahili"),
    ("ta", "Tamil"),
    ("te", "Telugu"),
    ("th", "Thai"),
    ("tr", "Turkish"),
    ("uk", "Ukrainian"),
    ("ur", "Urdu"),
    ("vi", "Vietnamese"),
    ("zh", "Chinese"),
    ("zu", "Zulu"),
)

# These entries predate this broader catalogue in the development seed.  A
# downgrade must not disable a language an operator had already configured.
LEGACY_LANGUAGE_CODES = {"en", "es", "fr", "ko", "pt"}


def _languages() -> sa.Table:
    return sa.table(
        "creator_languages",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("code", sa.String()),
        sa.column("label", sa.String()),
        sa.column("enabled", sa.Boolean()),
    )


def upgrade() -> None:
    languages = _languages()
    for code, label in LANGUAGES:
        statement = postgresql.insert(languages).values(
            id=uuid4(), code=code, label=label, enabled=True
        )
        op.execute(
            statement.on_conflict_do_update(
                index_elements=["code"],
                set_={"label": label, "enabled": True},
            )
        )


def downgrade() -> None:
    added_codes = [code for code, _ in LANGUAGES if code not in LEGACY_LANGUAGE_CODES]
    languages = _languages()
    op.execute(
        sa.update(languages).where(languages.c.code.in_(added_codes)).values(enabled=False)
    )
