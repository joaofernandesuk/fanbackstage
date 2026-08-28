import { describe, expect, it } from "vitest";

import type { ComplianceStatus } from "../lib/compliance-api";
import {
  effectiveAgeNeedsAction,
  effectiveCreatorNeedsAction,
  payoutKycPolicyStatus,
  verificationNeedsAction,
} from "./compliance-status-card";

describe("verificationNeedsAction", () => {
  it("requires action for absent or non-current verification states", () => {
    expect(verificationNeedsAction(null)).toBe(true);
    expect(verificationNeedsAction("failed")).toBe(true);
    expect(verificationNeedsAction("expired")).toBe(true);
    expect(verificationNeedsAction("revoked")).toBe(true);
    expect(verificationNeedsAction("review_required")).toBe(true);
  });

  it("does not relabel a current provider result as action required", () => {
    expect(verificationNeedsAction("verified")).toBe(false);
    expect(verificationNeedsAction("pending")).toBe(false);
  });
});

describe("effectiveAgeNeedsAction", () => {
  it("uses the current policy decision even when the provider record is verified", () => {
    const status = {
      fan_age_verification: { status: "verified" },
      adult_media_decision: {
        allowed: false,
        code: "AGE_ASSURANCE_INSUFFICIENT",
        action: "VERIFY_AGE",
        reason: "Stronger age assurance is required.",
      },
    } as ComplianceStatus;

    expect(effectiveAgeNeedsAction(status)).toBe(true);
  });

  it("does not demand re-verification when the current resolver allows access", () => {
    const status = {
      fan_age_verification: null,
      adult_media_decision: { allowed: true },
    } as ComplianceStatus;

    expect(effectiveAgeNeedsAction(status)).toBe(false);
  });
});

describe("effectiveCreatorNeedsAction", () => {
  const profile = {
    verification_status: "verified",
    adult_verified: true,
    creator_compliance: {
      jurisdiction: "PT",
      policy_version: 3,
      verification_status: "verified",
      verification_expires_at: "2026-08-01T00:00:00Z",
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
  };

  it("does not let a raw verified provider row hide an effective creator denial", () => {
    expect(effectiveCreatorNeedsAction(profile)).toBe(true);
  });

  it("uses the canonical current identity and age decision", () => {
    expect(effectiveCreatorNeedsAction({
      ...profile,
      creator_compliance_action_required: false,
      creator_compliance: {
        ...profile.creator_compliance,
        identity_allowed: true,
        age_allowed: true,
        public_allowed: true,
        code: "CREATOR_COMPLIANCE_ALLOWED",
      },
    })).toBe(false);
  });

  it("preserves action-required state for unresolved performer or consent content", () => {
    expect(effectiveCreatorNeedsAction({
      ...profile,
      performer_consent_issue_count: 2,
      creator_compliance_action_required: true,
      creator_compliance: {
        ...profile.creator_compliance,
        identity_allowed: true,
        age_allowed: true,
        public_allowed: true,
        payout_kyc_satisfied: true,
        code: "CREATOR_COMPLIANCE_ALLOWED",
      },
    })).toBe(true);
  });
});

describe("payoutKycPolicyStatus", () => {
  it("distinguishes policy satisfaction from an unconfigured payout rail", () => {
    const eligibility = {
      jurisdiction: "PT",
      policy_version: 3,
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

    expect(payoutKycPolicyStatus(eligibility)).toBe("Satisfied");
    expect(payoutKycPolicyStatus({
      ...eligibility,
      payout_kyc_satisfied: false,
      payout_code: "PAYOUT_KYC_REQUIRED",
    })).toBe("Action required");
    expect(payoutKycPolicyStatus({
      ...eligibility,
      payout_kyc_required: false,
    })).toBe("Not required");
  });
});
