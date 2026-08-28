export const assuranceLevels = ["none", "self_attested", "low", "medium", "high"] as const;
export type AssuranceLevel = (typeof assuranceLevels)[number];

export const policyStatuses = ["draft", "scheduled", "active", "retired"] as const;
export type PolicyStatus = (typeof policyStatuses)[number];

export const complianceFeatures = [
  "platform_access",
  "new_fan_registration",
  "creator_registration",
  "purchases",
  "subscriptions",
  "ppv",
  "live",
  "marketplace",
  "featuring",
  "marketing_email",
  "messaging",
  "adult_media",
] as const;
export type ComplianceFeature = (typeof complianceFeatures)[number];

export const verificationStatuses = [
  "pending",
  "verified",
  "failed",
  "expired",
  "revoked",
  "review_required",
] as const;
export type VerificationStatus = (typeof verificationStatuses)[number];

export type PolicyRules = {
  enabled: boolean;
  registration_allowed: boolean;
  creator_registration_allowed: boolean;
  purchases_allowed: boolean;
  subscriptions_allowed: boolean;
  ppv_allowed: boolean;
  live_allowed: boolean;
  marketplace_allowed: boolean;
  featuring_allowed: boolean;
  marketing_email_allowed: boolean;
  messaging_allowed: boolean;
  minimum_age: number;
  fan_age_verification_required: boolean;
  anonymous_adult_preview_allowed: boolean;
  required_assurance_level: AssuranceLevel;
  reverify_after_days: number | null;
  grace_period_days: number;
  creator_identity_required: boolean;
  creator_age_verification_required: boolean;
  payout_kyc_required: boolean;
  co_performer_verification_required: boolean;
  release_required: boolean;
  explicit_public_preview_allowed: boolean;
  restricted_media_policy: string;
  age_provider: string;
  provider_policy_key: string | null;
};

export type PolicyOverrides = Partial<PolicyRules>;
export type PolicyRuleKey = keyof PolicyRules;
export type PolicyRuleGroup = "Access" | "Age assurance" | "Creator" | "Performers" | "Provider";
export type PolicyRuleDefinition = {
  key: PolicyRuleKey;
  label: string;
  description: string;
  group: PolicyRuleGroup;
  kind: "boolean" | "number" | "nullable-number" | "assurance" | "text" | "nullable-text";
  minimum?: number;
  maximum?: number;
};

export const policyRuleDefinitions: readonly PolicyRuleDefinition[] = [
  { key: "enabled", label: "Platform access", description: "Master jurisdiction access rule.", group: "Access", kind: "boolean" },
  { key: "registration_allowed", label: "Fan registration", description: "Allow new fan accounts.", group: "Access", kind: "boolean" },
  { key: "creator_registration_allowed", label: "Creator registration", description: "Allow new creator onboarding.", group: "Access", kind: "boolean" },
  { key: "purchases_allowed", label: "Purchases", description: "Master control for new paid actions.", group: "Access", kind: "boolean" },
  { key: "subscriptions_allowed", label: "Subscriptions", description: "Allow new subscriptions and covered renewals.", group: "Access", kind: "boolean" },
  { key: "ppv_allowed", label: "PPV", description: "Allow pay-per-view purchases.", group: "Access", kind: "boolean" },
  { key: "live_allowed", label: "Live", description: "Allow Live access and participation.", group: "Access", kind: "boolean" },
  { key: "marketplace_allowed", label: "Marketplace", description: "Allow marketplace access and purchases.", group: "Access", kind: "boolean" },
  { key: "featuring_allowed", label: "Featuring", description: "Allow paid featuring bookings.", group: "Access", kind: "boolean" },
  { key: "marketing_email_allowed", label: "Marketing email", description: "Allow jurisdiction-eligible marketing delivery.", group: "Access", kind: "boolean" },
  { key: "messaging_allowed", label: "Messaging", description: "Allow messaging features.", group: "Access", kind: "boolean" },
  { key: "minimum_age", label: "Minimum age", description: "Configured operational minimum; not a statement of law.", group: "Age assurance", kind: "number", minimum: 1, maximum: 120 },
  { key: "fan_age_verification_required", label: "Fan age verification", description: "Require provider-backed viewer age assurance.", group: "Age assurance", kind: "boolean" },
  { key: "anonymous_adult_preview_allowed", label: "Anonymous adult preview", description: "Allow restricted previews before account linking.", group: "Age assurance", kind: "boolean" },
  { key: "required_assurance_level", label: "Required assurance", description: "Minimum acceptable assurance strength.", group: "Age assurance", kind: "assurance" },
  { key: "reverify_after_days", label: "Re-verify after days", description: "Null means no policy interval; provider validity still applies.", group: "Age assurance", kind: "nullable-number", minimum: 1, maximum: 3650 },
  { key: "grace_period_days", label: "Grace period days", description: "Configured transition grace period.", group: "Age assurance", kind: "number", minimum: 0, maximum: 365 },
  { key: "explicit_public_preview_allowed", label: "Explicit public preview", description: "Allow an explicit-content preview projection.", group: "Age assurance", kind: "boolean" },
  { key: "restricted_media_policy", label: "Restricted media policy reference", description: "Reviewed reference reserved for a named media-policy adapter; it is not an executable switch today.", group: "Age assurance", kind: "text" },
  { key: "creator_identity_required", label: "Creator identity / KYC", description: "Require creator identity verification.", group: "Creator", kind: "boolean" },
  { key: "creator_age_verification_required", label: "Creator age verification", description: "Require creator adult verification separately from identity.", group: "Creator", kind: "boolean" },
  { key: "payout_kyc_required", label: "Payout KYC", description: "Require creator KYC for payout eligibility.", group: "Creator", kind: "boolean" },
  { key: "co_performer_verification_required", label: "Co-performer verification", description: "Require linked performer verification.", group: "Performers", kind: "boolean" },
  { key: "release_required", label: "Consent / release", description: "Require a valid linked release.", group: "Performers", kind: "boolean" },
  { key: "age_provider", label: "Age provider", description: "Provider adapter selector, never a credential.", group: "Provider", kind: "text" },
  { key: "provider_policy_key", label: "Provider policy reference", description: "Non-secret provider policy/configuration reference.", group: "Provider", kind: "nullable-text" },
] as const;

export const safeBlockedPolicyDraft: PolicyRules = {
  enabled: false,
  registration_allowed: false,
  creator_registration_allowed: false,
  purchases_allowed: false,
  subscriptions_allowed: false,
  ppv_allowed: false,
  live_allowed: false,
  marketplace_allowed: false,
  featuring_allowed: false,
  marketing_email_allowed: false,
  messaging_allowed: false,
  minimum_age: 18,
  fan_age_verification_required: true,
  anonymous_adult_preview_allowed: false,
  required_assurance_level: "high",
  reverify_after_days: 365,
  grace_period_days: 0,
  creator_identity_required: true,
  creator_age_verification_required: true,
  payout_kyc_required: true,
  co_performer_verification_required: true,
  release_required: true,
  explicit_public_preview_allowed: false,
  restricted_media_policy: "deny",
  age_provider: "verifymyage",
  provider_policy_key: null,
};

export type PageResult<T> = { items: T[]; page: number; page_size: number; total: number };

export type PolicyTemplate = { id: string; key: string; name: string; description: string | null };
export type TemplateRevision = {
  id: string;
  version: number;
  status: PolicyStatus;
  rules: PolicyRules;
  is_demo: boolean;
  effective_from: string;
  effective_until: string | null;
  reviewed_at: string | null;
  change_reason: string;
};
export type JurisdictionRevision = {
  id: string;
  country_code: string;
  version: number;
  template_revision_id: string;
  status: PolicyStatus;
  overrides: PolicyOverrides;
  is_demo: boolean;
  effective_from: string;
  effective_until: string | null;
  reviewed_at: string | null;
  change_reason: string;
};
export type EffectiveCountryPolicy = {
  id: string;
  version: number;
  status: PolicyStatus;
  template_revision_id: string;
  template_version: number;
  effective_from: string;
  effective_until: string | null;
  minimum_age: number;
  fan_age_verification_required: boolean;
  required_assurance_level: AssuranceLevel;
  creator_identity_required: boolean;
  creator_age_verification_required: boolean;
  payout_kyc_required: boolean;
  co_performer_verification_required: boolean;
  release_required: boolean;
  age_provider: string;
};
export type CountryPolicy = {
  code: string;
  name: string;
  enabled: boolean;
  effective_policy: EffectiveCountryPolicy | null;
};
export type FeatureFlagRevision = {
  id: string;
  feature: ComplianceFeature;
  country_scope: string | null;
  version: number;
  enabled: boolean;
  is_demo: boolean;
  effective_from: string;
  effective_until: string | null;
  change_reason: string;
};
export type VerificationAttempt = {
  id: string;
  user_id: string | null;
  anonymous: boolean;
  anonymous_session_id: string | null;
  provider: string;
  country_code: string;
  status: VerificationStatus;
  required_minimum_age: number;
  required_assurance: AssuranceLevel;
  achieved_minimum_age: number | null;
  achieved_assurance: AssuranceLevel;
  initiated_at: string;
  verified_at: string | null;
  failed_at: string | null;
  revoked_at: string | null;
  expires_at: string | null;
  failure_reason_code: string | null;
  retryable: boolean;
  applicable_policy_id: string;
  applicable_policy_version: number;
};
export type ProviderCapabilities = Record<string, boolean | string | number | null>;
export type ProviderProbeSummary = { status: string; error_code: string | null; probed_at: string };
export type ProviderInventory = {
  provider: string;
  selected: boolean;
  enabled: boolean;
  environment: string | null;
  configuration_complete: boolean;
  capabilities: ProviderCapabilities | null;
  latest_probe: ProviderProbeSummary | null;
  last_healthy_at: string | null;
};
export type ProviderProbe = {
  id: string;
  provider: string;
  environment: string;
  status: string;
  configuration_complete: boolean;
  callback_url: string | null;
  error_code: string | null;
  probed_at: string;
  capabilities: ProviderCapabilities;
};
export type SimulationDecision = {
  allowed: boolean;
  code: string;
  action: string | null;
  reason: string;
  feature: ComplianceFeature;
  jurisdiction: string | null;
  policy_version: number | null;
  required_minimum_age: number | null;
  required_assurance_level: AssuranceLevel;
  achieved_assurance_level: AssuranceLevel;
  age_access_allowed: boolean;
  feature_allowed: boolean;
  verification_expires_at: string | null;
};
export type ComplianceAuditEvent = {
  id: string;
  event_type: string;
  actor_user_id: string | null;
  target_type: string | null;
  target_id: string | null;
  correlation_id: string | null;
  ip_address: string | null;
  user_agent: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type PolicyWeakening = {
  field: PolicyRuleKey;
  label: string;
  before: PolicyRules[PolicyRuleKey];
  after: PolicyRules[PolicyRuleKey];
};

const accessEnableFields = new Set<PolicyRuleKey>([
  "enabled",
  "registration_allowed",
  "creator_registration_allowed",
  "purchases_allowed",
  "subscriptions_allowed",
  "ppv_allowed",
  "live_allowed",
  "marketplace_allowed",
  "featuring_allowed",
  "marketing_email_allowed",
  "messaging_allowed",
  "anonymous_adult_preview_allowed",
  "explicit_public_preview_allowed",
]);
const requirementFields = new Set<PolicyRuleKey>([
  "fan_age_verification_required",
  "creator_identity_required",
  "creator_age_verification_required",
  "payout_kyc_required",
  "co_performer_verification_required",
  "release_required",
]);
const assuranceStrength: Record<AssuranceLevel, number> = {
  none: 0,
  self_attested: 1,
  low: 2,
  medium: 3,
  high: 4,
};

export function mergePolicyRules(base: PolicyRules, overrides: PolicyOverrides): PolicyRules {
  return { ...base, ...overrides };
}

export function policyWeakeningChanges(before: PolicyRules, after: PolicyRules): PolicyWeakening[] {
  const definitions = new Map(policyRuleDefinitions.map((definition) => [definition.key, definition]));
  const weakened = (Object.keys(before) as PolicyRuleKey[]).filter((field) => {
    const previous = before[field];
    const next = after[field];
    if (previous === next) return false;
    if (accessEnableFields.has(field)) return previous === false && next === true;
    if (requirementFields.has(field)) return previous === true && next === false;
    if (field === "minimum_age") return Number(next) < Number(previous);
    if (field === "required_assurance_level") {
      return assuranceStrength[next as AssuranceLevel] < assuranceStrength[previous as AssuranceLevel];
    }
    if (field === "reverify_after_days") {
      return previous !== null && (next === null || Number(next) > Number(previous));
    }
    if (field === "grace_period_days") return Number(next) > Number(previous);
    return false;
  });
  return weakened.map((field) => ({
    field,
    label: definitions.get(field)?.label ?? field,
    before: before[field],
    after: after[field],
  }));
}

export function featureFlagWeakens(previous: boolean | undefined, next: boolean): boolean {
  return next && previous !== true;
}

export function confirmedCompliancePayload<T extends Record<string, unknown>>(
  payload: T,
  changeReason: string,
): T & { change_reason: string; confirmation: "CONFIRM_COMPLIANCE_CHANGE" } {
  const normalizedReason = changeReason.trim();
  if (normalizedReason.length < 3) throw new Error("A durable compliance change reason is required.");
  return {
    ...payload,
    change_reason: normalizedReason,
    confirmation: "CONFIRM_COMPLIANCE_CHANGE",
  };
}

export function localDateTimeValue(value: string | Date | null = new Date()): string {
  if (!value) return "";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

export function optionalIso(value: FormDataEntryValue | null): string | null {
  const raw = String(value ?? "").trim();
  if (!raw) return null;
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) throw new Error("Enter a valid date and time.");
  return parsed.toISOString();
}

export function formatOperationalLabel(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase());
}

export function formatPolicyValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "Not set";
  if (typeof value === "boolean") return value ? "On" : "Off";
  return String(value).replaceAll("_", " ");
}

export function pageSummary(page: number, pageSize: number, total: number): string {
  if (total === 0) return "0 records";
  const start = (page - 1) * pageSize + 1;
  const end = Math.min(total, page * pageSize);
  return `${start}–${end} of ${total}`;
}

const sensitiveMetadataKey = /(secret|password|credential|authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|state[_-]?hash|result[_-]?metadata|provider[_-]?verification[_-]?id)/i;

export function safeAuditMetadata(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(safeAuditMetadata);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([key, nested]) => [
        key,
        sensitiveMetadataKey.test(key) ? "[redacted]" : safeAuditMetadata(nested),
      ]),
    );
  }
  return value;
}

export function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}
