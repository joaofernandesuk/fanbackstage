import { describe, expect, it } from "vitest";

import {
  canRunDevelopmentVerification,
  CreatorOnboardingProfile,
  creatorHasCurrentVerification,
  creatorOnboardingError,
  creatorProfilePayload,
} from "./creator-onboarding";
import { ApiError } from "../lib/api";
import { defaultTimezoneForCountry, regionForName, regionsForCountry } from "../lib/profile-location-catalog";

const pendingProfile: CreatorOnboardingProfile = {
  username: "creator-example",
  display_name: "Creator Example",
  bio: null,
  country_code: null,
  region: null,
  city: null,
  show_location: false,
  timezone: null,
  status: "pending_verification",
  is_public: false,
  verification_status: "not_started",
  adult_verified: false,
  creator_compliance: {
    jurisdiction: "PT",
    policy_version: 2,
    verification_status: null,
    verification_expires_at: null,
    identity_required: true,
    identity_allowed: false,
    age_required: true,
    age_allowed: false,
    public_allowed: false,
    payout_kyc_required: true,
    payout_kyc_satisfied: false,
    payout_allowed: false,
    code: "CREATOR_IDENTITY_VERIFICATION_REQUIRED",
    reason: "Current creator identity verification is required",
    payout_code: "PAYOUT_KYC_REQUIRED",
  },
  performer_consent_issue_count: 0,
  creator_compliance_action_required: true,
  rejection_reason: null,
  languages: [],
  categories: [],
  social_links: [],
  available_languages: [],
  available_categories: [],
  development_verification_available: false,
  staging_kyc_sandbox_available: false,
  staging_kyc_session_reference: null,
  staging_kyc_verification_id: null,
};

describe("creator onboarding", () => {
  it("shows the development verification action only from an explicit current capability", () => {
    expect(canRunDevelopmentVerification(pendingProfile)).toBe(false);
    expect(canRunDevelopmentVerification({
      ...pendingProfile,
      development_verification_available: true,
    })).toBe(true);
    expect(canRunDevelopmentVerification({
      ...pendingProfile,
      status: "pending_review",
      development_verification_available: true,
    })).toBe(false);
  });

  it("uses the current policy decision instead of the raw provider outcome", () => {
    expect(creatorHasCurrentVerification({
      ...pendingProfile,
      status: "approved",
      is_public: true,
      verification_status: "verified",
      adult_verified: true,
    })).toBe(false);
    expect(creatorHasCurrentVerification({
      ...pendingProfile,
      status: "approved",
      verification_status: "verified",
      adult_verified: true,
      creator_compliance: {
        ...pendingProfile.creator_compliance,
        verification_status: "verified",
        verification_expires_at: "2026-09-26T00:00:00Z",
        identity_allowed: true,
        age_allowed: true,
        public_allowed: true,
        payout_kyc_satisfied: true,
        code: "CREATOR_COMPLIANCE_ALLOWED",
        reason: "Creator identity and age requirements are satisfied",
        payout_code: "PAYOUT_NOT_CONFIGURED",
      },
    })).toBe(true);
  });

  it("uses a country default timezone and keeps a browser fallback for unknown countries", () => {
    expect(defaultTimezoneForCountry("PT", "America/New_York")).toBe("Europe/Lisbon");
    expect(defaultTimezoneForCountry("XX", "America/New_York")).toBe("America/New_York");
  });

  it("offers dependent region and city choices for each enabled local country", () => {
    expect(regionsForCountry("PT").map((region) => region.name)).toContain("Lisbon");
    expect(regionForName("PT", "Lisbon")?.cities).toContain("Cascais");
    expect(regionForName("US", "California")?.timezone).toBe("America/Los_Angeles");
    expect(regionForName("GB", "Scotland")?.cities).toContain("Edinburgh");
  });

  it("builds the complete authoritative profile payload", () => {
    const form = new FormData();
    form.set("username", " creator-example ");
    form.set("display_name", " Creator Example ");
    form.set("bio", " Studio updates ");
    form.set("country_code", " pt ");
    form.set("region", " Lisbon ");
    form.set("city", " Lisbon ");
    form.set("timezone", " Europe/Lisbon ");
    form.set("show_location", "on");
    form.append("category_slugs", "video-behind-scenes");
    form.append("language_codes", "en");
    form.append("social_label", " Portfolio ");
    form.append("social_url", " https://creator.example/portfolio ");
    form.set("is_public", "on");

    expect(creatorProfilePayload(form, true)).toEqual({
      username: "creator-example",
      display_name: "Creator Example",
      bio: "Studio updates",
      country_code: "PT",
      region: "Lisbon",
      city: "Lisbon",
      show_location: true,
      timezone: "Europe/Lisbon",
      category_slugs: ["video-behind-scenes"],
      language_codes: ["en"],
      social_links: [
        { label: "Portfolio", url: "https://creator.example/portfolio" },
      ],
      is_public: true,
    });
  });

  it("rejects a partial social link before mutation", () => {
    const form = new FormData();
    form.append("social_label", "Portfolio");
    form.append("social_url", "");
    expect(() => creatorProfilePayload(form, false)).toThrow(
      "Every social link needs both a label and a URL.",
    );
  });

  it("sends explicit nulls when optional profile text is cleared", () => {
    const form = new FormData();
    expect(creatorProfilePayload(form, false)).toMatchObject({
      bio: null,
      country_code: null,
      region: null,
      city: null,
      timezone: null,
    });
  });

  it("does not expose unknown server or runtime error details", () => {
    expect(creatorOnboardingError(
      new ApiError("database connection details", 500),
      "Unable to save the creator profile.",
    )).toBe("Unable to save the creator profile.");
    expect(creatorOnboardingError(
      new Error("internal browser detail"),
      "Unable to save the creator profile.",
    )).toBe("Unable to save the creator profile.");
    expect(creatorOnboardingError(
      new Error("Every social link needs both a label and a URL."),
      "Unable to save the creator profile.",
    )).toBe("Every social link needs both a label and a URL.");
  });
});
