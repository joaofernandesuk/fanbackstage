"use client";

import { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "../lib/api";
import { type ComplianceStatus, getComplianceStatus } from "../lib/compliance-api";
import {
  creatorComplianceIsCurrent,
  type CreatorComplianceEligibility,
  type CreatorComplianceProjection,
} from "../lib/creator-compliance";
import { AdultAccessGate } from "./adult-access-gate";
import styles from "./compliance-status-card.module.css";

export function verificationNeedsAction(status: string | null | undefined) {
  return !status || ["failed", "expired", "revoked", "review_required"].includes(status);
}

export function effectiveAgeNeedsAction(status: ComplianceStatus | null | undefined) {
  return Boolean(status && !status.adult_media_decision.allowed);
}

export function effectiveCreatorNeedsAction(
  profile: CreatorComplianceProjection | null | undefined,
) {
  return Boolean(profile?.creator_compliance_action_required);
}

export function payoutKycPolicyStatus(
  eligibility: CreatorComplianceEligibility | null | undefined,
) {
  if (!eligibility) return "Unavailable";
  if (!eligibility.payout_kyc_required) return "Not required";
  return eligibility.payout_kyc_satisfied ? "Satisfied" : "Action required";
}

function readable(value: string) {
  return value.replaceAll("_", " ").replace(/(^|\s)\S/g, (letter) => letter.toUpperCase());
}

function StatusBadge({ value }: { value: string }) {
  return <span className={styles.badge} data-status={value}>{readable(value)}</span>;
}

export function ComplianceStatusCard({ creator = false }: { creator?: boolean }) {
  const [status, setStatus] = useState<ComplianceStatus | null>(null);
  const [creatorProfile, setCreatorProfile] = useState<CreatorComplianceProjection | null>(null);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [nextStatus, nextCreatorProfile] = await Promise.all([
        getComplianceStatus(),
        creator
          ? api<CreatorComplianceProjection>("/creators/me")
          : Promise.resolve(null),
      ]);
      setStatus(nextStatus);
      setCreatorProfile(nextCreatorProfile);
      setError("");
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Compliance status is temporarily unavailable.",
      );
    }
  }, [creator]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const fan = status?.fan_age_verification;
  const identity = status?.creator_identity_verification;
  const effectiveAge = status?.adult_media_decision;
  const creatorEligibility = creatorProfile?.creator_compliance;

  return (
    <section aria-labelledby={creator ? "creator-compliance-title" : "account-compliance-title"} className={`${styles.card} card`}>
      <p className="eyebrow">COMPLIANCE STATUS</p>
      <h2 id={creator ? "creator-compliance-title" : "account-compliance-title"}>
        {creator ? "Creator compliance" : "Age assurance"}
      </h2>
      {error ? (
        <div className={styles.error} role="alert">
          <p>{error}</p>
          <button onClick={() => void refresh()} type="button">Try again</button>
        </div>
      ) : !status ? <p>Checking server-authoritative status…</p> : (
        <div className={styles.grid}>
          <article>
            <div className={styles.row}>
              <h3>Fan/viewer age assurance</h3>
              <StatusBadge value={fan?.status ?? "not_started"} />
            </div>
            {fan ? (
              <dl>
                <div><dt>Country</dt><dd>{fan.country_code}</dd></div>
                <div><dt>Assurance</dt><dd>{readable(fan.achieved_assurance_level)}</dd></div>
                <div><dt>Expires</dt><dd>{fan.expires_at ? new Date(fan.expires_at).toLocaleString() : "No date supplied"}</dd></div>
              </dl>
            ) : <p>No provider verification is linked to this account.</p>}
            {effectiveAge ? (
              <p className={styles.note}>
                Current access decision: {effectiveAge.allowed ? "Allowed" : readable(effectiveAge.code)}
              </p>
            ) : null}
            {effectiveAgeNeedsAction(status) ? (
              <AdultAccessGate
                access={{
                  compliance_allowed: false,
                  compliance_code: effectiveAge?.code,
                  compliance_action: effectiveAge?.action,
                  compliance_reason: effectiveAge?.reason,
                  adult_access_required: true,
                  adult_access_granted: false,
                }}
                feature="adult_media"
                onGranted={refresh}
                title="restricted FanBackstage experiences"
              />
            ) : null}
          </article>

          {creator ? (
            <>
              <article>
                <div className={styles.row}>
                  <h3>Creator identity / KYC</h3>
                  <StatusBadge
                    value={creatorEligibility
                      ? (creatorComplianceIsCurrent(creatorProfile)
                        ? "verified"
                        : "action_required")
                      : "unavailable"}
                  />
                </div>
                {creatorEligibility ? (
                  <dl>
                    <div><dt>Current identity rule</dt><dd>{creatorEligibility.identity_required ? (creatorEligibility.identity_allowed ? "Satisfied" : "Action required") : "Not required"}</dd></div>
                    <div><dt>Current age rule</dt><dd>{creatorEligibility.age_required ? (creatorEligibility.age_allowed ? "Satisfied" : "Action required") : "Not required"}</dd></div>
                    <div><dt>Jurisdiction</dt><dd>{creatorEligibility.jurisdiction ?? "Unresolved"}</dd></div>
                    <div><dt>Effective expiry</dt><dd>{creatorEligibility.verification_expires_at ? new Date(creatorEligibility.verification_expires_at).toLocaleString() : "No current verified expiry"}</dd></div>
                    <div><dt>Latest provider evidence</dt><dd>{readable(identity?.status ?? creatorProfile?.verification_status ?? "not_started")}</dd></div>
                  </dl>
                ) : <p>No effective creator compliance decision is available.</p>}
                {creatorEligibility ? <p className={styles.note}>{creatorEligibility.reason}</p> : null}
                <p className={styles.note}>Creator identity/KYC is separate from viewer age assurance and content entitlement.</p>
              </article>
              <article>
                <div className={styles.row}>
                  <h3>Payout, performers, and consent</h3>
                  <StatusBadge
                    value={effectiveCreatorNeedsAction(creatorProfile)
                      ? "action_required"
                      : "verified"}
                  />
                </div>
                {creatorEligibility && creatorProfile ? (
                  <dl>
                    <div>
                      <dt>Payout KYC policy</dt>
                      <dd>{payoutKycPolicyStatus(creatorEligibility)}</dd>
                    </div>
                    <div>
                      <dt>Payout rail</dt>
                      <dd>{creatorEligibility.payout_allowed
                        ? "Allowed"
                        : readable(creatorEligibility.payout_code)}</dd>
                    </div>
                    <div>
                      <dt>Content with performer / consent issues</dt>
                      <dd>{creatorProfile.performer_consent_issue_count}</dd>
                    </div>
                    <div>
                      <dt>Current action state</dt>
                      <dd>{creatorProfile.creator_compliance_action_required
                        ? "Action required"
                        : "No compliance action required"}</dd>
                    </div>
                  </dl>
                ) : <p>No effective creator readiness summary is available.</p>}
                <p className={styles.note}>
                  Payout KYC satisfaction does not enable payouts while the payout rail is not configured. Each issue count represents creator-owned content whose current performer or consent authority fails closed.
                </p>
              </article>
            </>
          ) : null}
        </div>
      )}
    </section>
  );
}
