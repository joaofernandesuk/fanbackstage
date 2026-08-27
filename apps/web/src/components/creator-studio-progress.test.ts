import { describe, expect, it } from "vitest";

import { creatorProgressItems, CreatorProgressSnapshot } from "./creator-studio-progress";

const incomplete: CreatorProgressSnapshot = {
  profile: null,
  content: [],
  posts: [],
  subscriptionOptions: [],
  liveSettings: null,
};

describe("creator Studio progress", () => {
  it("derives no completion from missing application state", () => {
    expect(creatorProgressItems(incomplete).every((item) => !item.complete)).toBe(true);
  });

  it("derives completion from authoritative domain records", () => {
    const items = creatorProgressItems({
      profile: {
        username: "luna",
        display_name: "Luna",
        bio: "Nightly creator streams",
        status: "approved",
        is_public: true,
        verification_status: "verified",
        adult_verified: true,
      },
      content: [
        {
          access_policy: "ppv",
          content_type: "gallery",
          status: "published",
        },
        {
          access_policy: "subscription",
          content_type: "video",
          preview_duration_seconds: 12,
          status: "processing",
        },
      ],
      posts: [{ status: "published" }],
      subscriptionOptions: [{ duration: "month_1" }],
      liveSettings: { private_sessions_enabled: true },
    });

    expect(items).toHaveLength(7);
    expect(items.every((item) => item.complete)).toBe(true);
    expect(items.map((item) => item.href)).toEqual([
      "/creator-onboarding",
      "#posts",
      "#subscriptions",
      "#media-content",
      "#media-content",
      "#media-content",
      "#live",
    ]);
  });

  it("does not infer PPV earning readiness from a draft or a missing video preview", () => {
    const items = creatorProgressItems({
      ...incomplete,
      content: [
        {
          access_policy: "ppv",
          content_type: "gallery",
          status: "processing",
        },
        {
          access_policy: "subscription",
          content_type: "video",
          preview_duration_seconds: null,
          status: "published",
        },
      ],
    });

    expect(items.find((item) => item.title === "Publish your first PPV release")?.complete)
      .toBe(false);
    expect(items.find((item) => item.title === "Configure a video preview")?.complete)
      .toBe(false);
  });

  it("does not count draft posts or an unapproved profile", () => {
    const items = creatorProgressItems({
      ...incomplete,
      profile: {
        username: "draft",
        display_name: "Draft Creator",
        bio: "Still applying",
        status: "pending_review",
        is_public: false,
        verification_status: "verified",
        adult_verified: true,
      },
      posts: [{ status: "draft" }],
    });

    expect(items[0].complete).toBe(false);
    expect(items[1].complete).toBe(false);
  });

  it("does not call a public profile complete after creator KYC becomes stale", () => {
    const items = creatorProgressItems({
      ...incomplete,
      profile: {
        username: "stale-creator",
        display_name: "Stale Creator",
        bio: "Saved profile",
        status: "approved",
        is_public: true,
        verification_status: "failed",
        adult_verified: false,
      },
    });

    expect(items[0].complete).toBe(false);
  });
});
