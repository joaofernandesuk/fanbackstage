"""Isolated lifecycle driver for the real-stack Story browser journey."""

import asyncio
import json
import os
import sys
from uuid import UUID

if os.environ.get("FANBACKSTAGE_E2E_STORY_VALIDATION") != "1":
    raise SystemExit("Story E2E harness requires isolated validation")

from app.db.session import SessionLocal
from app.models.story import Story
from app.stories.service import expire_due_stories


async def main() -> dict[str, int | str]:
    command, story_id = sys.argv[1:]
    if command != "expire":
        raise ValueError("Unknown Story validation command")
    async with SessionLocal() as db:
        story = await db.get(Story, UUID(story_id))
        if not story:
            raise ValueError("Story not found")
        count = await expire_due_stories(db, now=story.expires_at)
        await db.commit()
        await db.refresh(story)
        return {"expired": count, "target_status": story.status.value}


print(json.dumps(asyncio.run(main())))
