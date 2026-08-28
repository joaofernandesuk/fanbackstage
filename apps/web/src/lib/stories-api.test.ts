import { describe, expect, it } from "vitest";

import {
  groupStoriesByCreator,
  storyAgeLabel,
  storyMediaUrl,
  storyReportPath,
  storyRailPath,
  type PublicStory,
  type StoryMedia,
} from "./stories-api";

const NOW = Date.parse("2026-08-26T12:00:00Z");

function media(id = "00000000-0000-0000-0000-000000000001"): StoryMedia {
  return {
    derivative_id: "00000000-0000-0000-0000-000000000020",
    mime_type: "image/jpeg",
    delivery_path: `/stories/${id}/media`,
  };
}

function story(overrides: Partial<PublicStory> = {}): PublicStory {
  return {
    id: "00000000-0000-0000-0000-000000000001",
    status: "active",
    creator: {
      id: "00000000-0000-0000-0000-000000000010",
      username: "luna-sparks",
      display_name: "Luna Sparks",
      avatar_reference: "/demo/creators/luna-sparks/avatar.jpg",
      verified: true,
    },
    media_type: "image",
    caption: "Studio light",
    alt_text: "Luna beside a studio light",
    access_policy: "free",
    created_at: "2026-08-26T10:59:00Z",
    published_at: "2026-08-26T11:00:00Z",
    expires_at: "2026-08-27T11:00:00Z",
    media: media(),
    compliance_allowed: true,
    compliance_code: "ALLOWED",
    compliance_action: null,
    compliance_reason: "Policy allows access",
    ...overrides,
  };
}

describe("Story public API helpers", () => {
  it("serializes a bounded rail request and cursor", () => {
    const path = storyRailPath({ limit: 500, cursor: "next page", creatorUsername: " Luna-Sparks " });
    const query = new URLSearchParams(path.split("?")[1]);
    expect(path.split("?")[0]).toBe("/stories/rail");
    expect(query.get("limit")).toBe("50");
    expect(query.get("cursor")).toBe("next page");
    expect(query.get("creator_username")).toBe("luna-sparks");
  });

  it("targets Story reports through the existing social moderation boundary", () => {
    expect(storyReportPath("story/id")).toBe("/feed/reports/story/story%2Fid");
  });

  it("accepts only the authoritative Story delivery path and matching media type", () => {
    expect(storyMediaUrl(story())).toBe(
      "http://localhost:8000/api/v1/stories/00000000-0000-0000-0000-000000000001/media",
    );
    expect(storyMediaUrl(story({ media: { ...media(), delivery_path: "https://files.example/original.jpg" } }))).toBeUndefined();
    expect(storyMediaUrl(story({ media_type: "video" }))).toBeUndefined();
    expect(storyMediaUrl(story({ compliance_allowed: false, media: null }))).toBeUndefined();
  });

  it("groups accessible active records without fabricating expired cards", () => {
    const second = story({ id: "00000000-0000-0000-0000-000000000002", media: media("00000000-0000-0000-0000-000000000002") });
    const expired = story({ id: "00000000-0000-0000-0000-000000000003", expires_at: "2026-08-26T11:59:59Z", media: media("00000000-0000-0000-0000-000000000003") });
    const groups = groupStoriesByCreator([story(), second, expired], NOW);
    expect(groups).toHaveLength(1);
    expect(groups[0].stories.map((item) => item.id)).toEqual([
      "00000000-0000-0000-0000-000000000001",
      "00000000-0000-0000-0000-000000000002",
    ]);
  });

  it("formats compact chronology from published_at", () => {
    expect(storyAgeLabel("2026-08-26T11:59:45Z", NOW)).toBe("Now");
    expect(storyAgeLabel("2026-08-26T11:42:00Z", NOW)).toBe("18m");
    expect(storyAgeLabel("2026-08-26T07:00:00Z", NOW)).toBe("5h");
  });
});
