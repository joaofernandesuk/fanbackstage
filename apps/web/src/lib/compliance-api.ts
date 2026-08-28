import { api, ApiError } from "./api";

export type ComplianceFeature =
  | "platform_access"
  | "new_fan_registration"
  | "creator_registration"
  | "purchases"
  | "subscriptions"
  | "ppv"
  | "live"
  | "marketplace"
  | "featuring"
  | "marketing_email"
  | "messaging"
  | "adult_media";

export type ComplianceAccess = {
  compliance_allowed: boolean;
  compliance_code: string;
  compliance_action: string | null;
  compliance_reason: string | null;
  adult_access_required?: boolean;
  adult_access_granted?: boolean;
};

export type ComplianceDecision = {
  allowed: boolean;
  code: string;
  action: string | null;
  reason: string;
  feature: ComplianceFeature;
  jurisdiction: string | null;
  policy_version: number | null;
  required_minimum_age: number | null;
  required_assurance_level: string;
  achieved_assurance_level: string;
  age_access_allowed: boolean;
  feature_allowed: boolean;
  verification_expires_at: string | null;
};

export type VerificationSummary = {
  verification_id: string;
  provider: string;
  status: "pending" | "verified" | "failed" | "expired" | "revoked" | "review_required";
  country_code: string;
  required_minimum_age: number;
  required_assurance_level: string;
  achieved_minimum_age: number | null;
  achieved_assurance_level: string;
  initiated_at: string;
  verified_at: string | null;
  expires_at: string | null;
  failure_reason_code: string | null;
  retryable: boolean;
};

export type CreatorIdentitySummary = {
  status: string;
  provider: string;
  identity_verified: boolean;
  adult_verified: boolean;
  country_code: string | null;
  verified_at: string | null;
  expires_at: string | null;
};

export type ComplianceStatus = {
  fan_age_verification: VerificationSummary | null;
  adult_media_decision: ComplianceDecision;
  creator_identity_verification: CreatorIdentitySummary | null;
  payout_eligibility: "not_configured";
  performer_verification_issue_count: number | null;
  consent_release_issue_count: number | null;
};

export type ComplianceCountry = { code: string; name: string };

export type AgeVerificationStart = {
  verification_id: string;
  provider: string;
  status: VerificationSummary["status"];
  authorization_url: string;
  country_code: string;
  required_minimum_age: number;
  required_assurance_level: string;
  anonymous_session_expires_at: string | null;
};

export function complianceAccessFromError(error: ApiError): ComplianceAccess {
  return {
    compliance_allowed: false,
    compliance_code: error.code ?? "POLICY_UNAVAILABLE",
    compliance_action: error.action ?? null,
    compliance_reason: error.reason ?? error.message,
    adult_access_required: error.action === "VERIFY_AGE",
    adult_access_granted: false,
  };
}

export function currentSafeReturnPath(location: Pick<Location, "pathname" | "search">): string {
  const path = `${location.pathname}${location.search}`;
  return path.startsWith("/") && !path.startsWith("//") ? path : "/";
}

export function safeProviderAuthorizationUrl(value: string): string | null {
  try {
    const parsed = new URL(value);
    if (parsed.protocol === "https:") return parsed.href;
    const loopbackHosts = new Set(["localhost", "127.0.0.1", "[::1]"]);
    return parsed.protocol === "http:" && loopbackHosts.has(parsed.hostname) ? parsed.href : null;
  } catch {
    return null;
  }
}

export function getComplianceCountries(): Promise<ComplianceCountry[]> {
  return api<ComplianceCountry[]>("/compliance/countries");
}

export function getComplianceDecision(
  feature: ComplianceFeature,
  adultRestricted: boolean,
  countryCode?: string,
): Promise<ComplianceDecision> {
  const query = new URLSearchParams({
    feature,
    adult_restricted: String(adultRestricted),
  });
  if (countryCode) query.set("country_code", countryCode);
  return api<ComplianceDecision>(`/compliance/decision?${query.toString()}`);
}

export function getComplianceStatus(): Promise<ComplianceStatus> {
  return api<ComplianceStatus>("/compliance/age-verification/status");
}

export function startAgeVerification({
  countryCode,
  returnPath,
}: {
  countryCode?: string;
  returnPath: string;
}): Promise<AgeVerificationStart> {
  return api<AgeVerificationStart>("/compliance/age-verification/start", {
    method: "POST",
    body: JSON.stringify({
      country_code: countryCode || null,
      return_path: returnPath,
    }),
  });
}
