"""Immutable, fictional data used by the local development demo seed.

Keep stable keys (emails, slugs, titles, and codes) unchanged once published.  The
seed resolves rows through these keys so a second run converges instead of adding
another copy of the same demo object.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

DEMO_EMAIL_SUFFIX = "@demo.fanbackstage.local"
PASSWORD = "fanbackstage-demo-local-only"

TARGET_USER_COUNT = 40
TARGET_CREATOR_COUNT = 13
TARGET_PUBLIC_CREATOR_COUNT = 12
TARGET_PUBLISHED_POST_COUNT = 48
TARGET_PUBLISHED_CONTENT_COUNT = 27
TARGET_PUBLISHED_VIDEO_COUNT = 12
TARGET_PUBLISHED_LISTING_COUNT = 18
TARGET_GROUP_COUNT = 2
TARGET_ACTIVE_STORY_COUNT = 24
TARGET_EXPIRED_STORY_COUNT = 4


@dataclass(frozen=True)
class UserSeed:
    email: str
    roles: tuple[str, ...] = ()


@dataclass(frozen=True)
class CreatorSeed:
    slug: str
    display_name: str
    bio: str
    city: str
    category: str
    language_code: str
    language_label: str

    @property
    def email(self) -> str:
        return f"{self.slug}{DEMO_EMAIL_SUFFIX}"

    @property
    def avatar_reference(self) -> str:
        return f"/demo/creators/{self.slug}/avatar.jpg"

    @property
    def cover_reference(self) -> str:
        return f"/demo/creators/{self.slug}/cover.jpg"


@dataclass(frozen=True)
class GalleryShowcaseSeed:
    creator_slug: str
    title: str
    description: str
    access_policy: str
    price_amount_minor: int | None = None


CREATORS = (
    CreatorSeed(
        "luna-sparks",
        "Luna Sparks",
        "Color-led studio sets, warm conversations, and playful behind-the-scenes notes.",
        "Lisbon",
        "studio",
        "en",
        "English",
    ),
    CreatorSeed(
        "mira-nova",
        "Mira Nova",
        "A fictional rising creator sharing weekly portrait studies and candid studio diaries.",
        "Porto",
        "photography",
        "pt",
        "Portuguese",
    ),
    CreatorSeed(
        "ivy-ember",
        "Ivy Ember",
        "Intimate editorial storytelling, subscriber collections, and creative process notes.",
        "Coimbra",
        "editorial",
        "en",
        "English",
    ),
    CreatorSeed(
        "skye-live",
        "Skye Live",
        "Friendly live-show host with studio Q&As, rehearsals, and community sessions.",
        "Faro",
        "live",
        "en",
        "English",
    ),
    CreatorSeed(
        "nora-market",
        "Nora Market",
        "Collector of harmless studio keepsakes, signed prints, and limited demo merchandise.",
        "Braga",
        "marketplace",
        "pt",
        "Portuguese",
    ),
    CreatorSeed(
        "aria-group",
        "Aria Group",
        "A collaborative fictional creator represented by a local demo management group.",
        "Lisbon",
        "collaboration",
        "en",
        "English",
    ),
    CreatorSeed(
        "nova-blue",
        "Nova Blue",
        "Cool-toned fashion studies, creative direction notes, and cinematic mood boards.",
        "Porto",
        "fashion",
        "fr",
        "French",
    ),
    CreatorSeed(
        "zara-pulse",
        "Zara Pulse",
        "Energetic movement sessions, colorful rehearsals, and fan-led creative prompts.",
        "Setubal",
        "performance",
        "es",
        "Spanish",
    ),
    CreatorSeed(
        "sienna-ray",
        "Sienna Ray",
        "Golden-hour portrait stories, travel journals, and relaxed community updates.",
        "Evora",
        "lifestyle",
        "en",
        "English",
    ),
    CreatorSeed(
        "atlas-reed",
        "Atlas Reed",
        "Graphic studio experiments, monochrome sets, and practical production walkthroughs.",
        "Aveiro",
        "design",
        "en",
        "English",
    ),
    CreatorSeed(
        "valentina-cruz",
        "Valentina Cruz",
        "A bilingual editorial creator mixing bright sets, styling notes, and mini diaries.",
        "Lisbon",
        "editorial",
        "es",
        "Spanish",
    ),
    CreatorSeed(
        "sera-kim",
        "Sera Kim",
        "Minimal studio portraits, thoughtful creator notes, and quiet late-night sessions.",
        "Porto",
        "photography",
        "ko",
        "Korean",
    ),
    CreatorSeed(
        "reya-restricted",
        "Reya Restricted",
        "A fictional non-public profile retained solely for Trust & Safety review workflows.",
        "Lisbon",
        "editorial",
        "en",
        "English",
    ),
)

PUBLIC_CREATORS = CREATORS[:-1]
RESTRICTED_CREATOR = CREATORS[-1]
STORY_CREATORS = PUBLIC_CREATORS[:8]

GALLERY_SHOWCASES = (
    GalleryShowcaseSeed(
        "luna-sparks",
        "Open Studio Contact Sheet",
        "Four harmless color studies from a fictional daytime studio session.",
        "free",
    ),
    GalleryShowcaseSeed(
        "ivy-ember",
        "Members' Editorial Notebook",
        "A subscriber gallery used to verify ordered images and membership access.",
        "subscription",
    ),
    GalleryShowcaseSeed(
        "zara-pulse",
        "Neon Motion Collection",
        "A premium multi-image demo collection with an authorised locked teaser.",
        "ppv",
        1_299,
    ),
)

CORE_USERS = (
    UserSeed(f"admin{DEMO_EMAIL_SUFFIX}", ("admin", "super_admin")),
    UserSeed(f"moderator{DEMO_EMAIL_SUFFIX}", ("moderator",)),
    UserSeed(
        f"evidence-moderator{DEMO_EMAIL_SUFFIX}",
        ("moderator", "super_admin"),
    ),
    UserSeed(f"manager{DEMO_EMAIL_SUFFIX}", ("manager",)),
    UserSeed(f"newfan{DEMO_EMAIL_SUFFIX}"),
    UserSeed(f"subscriber{DEMO_EMAIL_SUFFIX}"),
    UserSeed(f"ppvbuyer{DEMO_EMAIL_SUFFIX}"),
    UserSeed(f"marketbuyer{DEMO_EMAIL_SUFFIX}"),
    UserSeed(f"socialfan{DEMO_EMAIL_SUFFIX}"),
    UserSeed(f"marketing-in{DEMO_EMAIL_SUFFIX}"),
    UserSeed(f"marketing-out{DEMO_EMAIL_SUFFIX}"),
)

FAN_USERS = tuple(UserSeed(f"fan{index:02d}{DEMO_EMAIL_SUFFIX}") for index in range(1, 17))
USERS = CORE_USERS + FAN_USERS + tuple(UserSeed(creator.email) for creator in CREATORS)

GROUPS = (
    ("luminous-house", "Luminous House", "aria-group", 8_000),
    ("north-star-agency", "North Star Agency", "valentina-cruz", 7_500),
)

LISTING_ITEMS = (
    "signed studio print",
    "limited postcard set",
    "behind-the-scenes zine",
    "collector photo card",
    "studio tote",
    "signed contact sheet",
)


def gallery_title(creator: CreatorSeed) -> str:
    return f"Studio Notes — {creator.display_name}"


def video_title(creator: CreatorSeed) -> str:
    return f"After Hours — {creator.display_name}"


def post_body(creator: CreatorSeed, position: int) -> str:
    templates = (
        "A fresh look from today’s set. #fanbackstage #behindthescenes",
        "What detail should I explore next? I’m reading every note. #creatorjournal",
        "New studio notes are ready—come behind the scenes with me. #newdrop",
        "A short after-hours cut from this week’s session. #video #fanbackstage",
    )
    return f"{creator.display_name}: {templates[position]}"


def listing_title(creator: CreatorSeed, position: int) -> str:
    return f"Demo {LISTING_ITEMS[position % len(LISTING_ITEMS)].title()} — {creator.display_name}"


def listing_count_for_creator(position: int) -> int:
    return 2 if position < 6 else 1


def story_caption(creator: CreatorSeed, position: int, *, historical: bool = False) -> str:
    moments = (
        "A quick look at today’s set",
        "A quiet moment between takes",
        "One detail from the creative process",
    )
    prefix = "Archive" if historical else "Story"
    return f"{prefix} {position + 1} — {creator.display_name}: {moments[position % len(moments)]}."


def story_cohort_idempotency_key(creator: CreatorSeed, position: int, reference: datetime) -> str:
    """Hour-granular key: active rows short-circuit reruns; expired cohorts always advance."""
    return f"demo-story-{creator.slug}-{reference:%Y%m%d%H}-{position + 1}"


def expected_listing_titles() -> tuple[str, ...]:
    return tuple(
        listing_title(creator, item_position)
        for creator_position, creator in enumerate(PUBLIC_CREATORS)
        for item_position in range(listing_count_for_creator(creator_position))
    )
