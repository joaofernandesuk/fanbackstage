import { describe, expect, it } from "vitest";

import { creatorProgressItems, CreatorProgressSnapshot } from "./creator-studio-progress";

const currentCreatorCompliance = {
  jurisdiction: "PT",
  policy_version: 2,
  verification_status: "verified",
  verification_expires_at: "2026-09-26T00:00:00Z",
  identity_required: true,
  identity_allowed: true,
  age_required: true,
  age_allowed: true,
  public_allowed: true,
  payout_kyc_required: true,
  payout_kyc_satisfied: true,
  payout_allowed: false,
  code: "CREATOR_COMPLIANCE_ALLOWED",
  reason: "Creator identity and age requirements are satisfied",
  payout_code: "PAYOUT_NOT_CONFIGURED",
};

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
        creator_compliance: currentCreatorCompliance,
        performer_consent_issue_count: 0,
        creator_compliance_action_required: false,
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
        creator_compliance: currentCreatorCompliance,
        performer_consent_issue_count: 0,
        creator_compliance_action_required: false,
      },
      posts: [{ status: "draft" }],
    });

    expect(items[0].complete).toBe(false);
    expect(items[1].complete).toBe(false);
  });

  it("does not call a raw verified provider row current after policy eligibility fails", () => {
    const items = creatorProgressItems({
      ...incomplete,
      profile: {
        username: "stale-creator",
        display_name: "Stale Creator",
        bio: "Saved profile",
        status: "approved",
        is_public: true,
        verification_status: "verified",
        adult_verified: true,
        creator_compliance: {
          ...currentCreatorCompliance,
          verification_expires_at: "2026-08-01T00:00:00Z",
          identity_allowed: false,
          public_allowed: false,
          code: "CREATOR_IDENTITY_VERIFICATION_REQUIRED",
          reason: "Current creator identity verification is required",
          payout_kyc_satisfied: false,
          payout_code: "PAYOUT_KYC_REQUIRED",
        },
        performer_consent_issue_count: 0,
        creator_compliance_action_required: true,
      },
    });

    expect(items[0].complete).toBe(false);
  });
});
