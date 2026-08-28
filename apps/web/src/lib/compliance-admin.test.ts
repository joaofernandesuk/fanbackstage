import { describe, expect, it } from "vitest";

import {
  confirmedCompliancePayload,
  featureFlagWeakens,
  mergePolicyRules,
  pageSummary,
  policyWeakeningChanges,
  safeAuditMetadata,
  safeBlockedPolicyDraft,
} from "./compliance-admin";

describe("compliance policy operations helpers", () => {
  it("builds the exact high-impact confirmation contract and normalizes the reason", () => {
    expect(confirmedCompliancePayload({ enabled: false }, "  Incident containment  ")).toEqual({
      enabled: false,
      change_reason: "Incident containment",
      confirmation: "CONFIRM_COMPLIANCE_CHANGE",
    });
    expect(() => confirmedCompliancePayload({ enabled: true }, "  ")).toThrow(/reason/i);
  });

  it("keeps template inheritance deterministic while applying explicit country overrides", () => {
    const effective = mergePolicyRules(safeBlockedPolicyDraft, {
      enabled: true,
      minimum_age: 21,
      provider_policy_key: null,
    });

    expect(effective.enabled).toBe(true);
    expect(effective.minimum_age).toBe(21);
    expect(effective.provider_policy_key).toBeNull();
    expect(effective.fan_age_verification_required).toBe(true);
  });

  it("flags access expansion and weaker assurance before an operator confirms the revision", () => {
    const next = {
      ...safeBlockedPolicyDraft,
      enabled: true,
      minimum_age: 17,
      required_assurance_level: "low" as const,
      reverify_after_days: null,
      release_required: false,
    };

    expect(policyWeakeningChanges(safeBlockedPolicyDraft, next).map((change) => change.field))
      .toEqual(expect.arrayContaining([
        "enabled",
        "minimum_age",
        "required_assurance_level",
        "reverify_after_days",
        "release_required",
      ]));
  });

  it("treats an access-enabling feature revision as weakening unless already enabled", () => {
    expect(featureFlagWeakens(undefined, true)).toBe(true);
    expect(featureFlagWeakens(false, true)).toBe(true);
    expect(featureFlagWeakens(true, true)).toBe(false);
    expect(featureFlagWeakens(true, false)).toBe(false);
  });

  it("redacts provider secrets and opaque verification references in audit metadata", () => {
    expect(safeAuditMetadata({
      change_reason: "scheduled policy change",
      api_key: "private",
      nested: { access_token: "private", status: "healthy" },
      changes: [{ field: "minimum_age", before: 18, after: 21 }],
    })).toEqual({
      change_reason: "scheduled policy change",
      api_key: "[redacted]",
      nested: { access_token: "[redacted]", status: "healthy" },
      changes: [{ field: "minimum_age", before: 18, after: 21 }],
    });
  });

  it("formats bounded pagination without overstating the final page", () => {
    expect(pageSummary(2, 25, 32)).toBe("26–32 of 32");
    expect(pageSummary(1, 25, 0)).toBe("0 records");
  });
});
