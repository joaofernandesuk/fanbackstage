export type CreatorComplianceEligibility = {
  jurisdiction: string | null;
  policy_version: number | null;
  verification_status: string | null;
  verification_expires_at: string | null;
  identity_required: boolean;
  identity_allowed: boolean;
  age_required: boolean;
  age_allowed: boolean;
  public_allowed: boolean;
  payout_kyc_required: boolean;
  payout_kyc_satisfied: boolean;
  payout_allowed: boolean;
  code: string;
  reason: string;
  payout_code: string;
};

export type CreatorComplianceProjection = {
  verification_status: string;
  adult_verified: boolean;
  creator_compliance: CreatorComplianceEligibility;
  performer_consent_issue_count: number;
  creator_compliance_action_required: boolean;
};

export function creatorComplianceIsCurrent(
  profile: CreatorComplianceProjection | null | undefined,
): boolean {
  return Boolean(
    profile?.creator_compliance.identity_allowed
      && profile.creator_compliance.age_allowed
      && profile.creator_compliance.public_allowed,
  );
}
