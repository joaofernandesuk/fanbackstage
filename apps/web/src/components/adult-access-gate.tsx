"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";

import { ApiError } from "../lib/api";
import {
  ComplianceAccess,
  ComplianceCountry,
  ComplianceDecision,
  ComplianceFeature,
  currentSafeReturnPath,
  getComplianceCountries,
  getComplianceDecision,
  safeProviderAuthorizationUrl,
  startAgeVerification,
} from "../lib/compliance-api";
import styles from "./adult-access-gate.module.css";

export function complianceGateMode(action: string | null | undefined) {
  if (action === "VERIFY_AGE") return "verify" as const;
  if (action === "LOGIN") return "login" as const;
  return "retry" as const;
}

export function AdultAccessGate({
  access,
  adultRestricted = true,
  feature = "adult_media",
  onGranted,
  title,
}: {
  access?: Partial<ComplianceAccess>;
  adultRestricted?: boolean;
  feature?: ComplianceFeature;
  onGranted: () => Promise<void> | void;
  title: string;
}) {
  const [countries, setCountries] = useState<ComplianceCountry[]>([]);
  const [country, setCountry] = useState("");
  const [decision, setDecision] = useState<ComplianceDecision | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const titleId = useId();
  const granted = useRef(false);
  const onGrantedRef = useRef(onGranted);
  onGrantedRef.current = onGranted;

  const refresh = useCallback(async (countryCode?: string) => {
    const next = await getComplianceDecision(feature, adultRestricted, countryCode);
    setDecision(next);
    if (!countryCode && next.jurisdiction) setCountry(next.jurisdiction);
    if (next.allowed && !granted.current) {
      granted.current = true;
      await onGrantedRef.current();
    }
    return next;
  }, [adultRestricted, feature]);

  useEffect(() => {
    let active = true;
    void getComplianceCountries().then((rows) => {
      if (active) setCountries(rows);
    }).catch(() => {
      // A known account/request jurisdiction can still start verification.
    });
    void refresh().catch((caught: unknown) => {
      if (active) {
        setError(caught instanceof ApiError ? caught.message : "Age access is temporarily unavailable.");
      }
    });
    return () => {
      active = false;
    };
  }, [refresh]);

  const code = decision?.code ?? access?.compliance_code ?? "AGE_VERIFICATION_REQUIRED";
  const action = decision?.action ?? access?.compliance_action ?? null;
  const reason = decision?.reason ?? access?.compliance_reason ?? "Age verification is required.";
  const minimumAge = decision?.required_minimum_age;
  const mode = complianceGateMode(action);
  const canVerify = mode === "verify";
  const ageAction = canVerify || mode === "login";

  async function verify() {
    setWorking(true);
    setError("");
    try {
      const started = await startAgeVerification({
        countryCode: country || undefined,
        returnPath: currentSafeReturnPath(window.location),
      });
      const target = safeProviderAuthorizationUrl(started.authorization_url);
      if (!target) throw new Error("The verification provider returned an invalid redirect.");
      window.location.assign(target);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : caught instanceof Error
            ? caught.message
            : "Age verification could not be started.",
      );
      setWorking(false);
    }
  }

  async function retry() {
    setWorking(true);
    setError("");
    try {
      await refresh(country || undefined);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Age access is temporarily unavailable.");
    } finally {
      setWorking(false);
    }
  }

  function login() {
    const next = currentSafeReturnPath(window.location);
    window.location.assign(`/login?next=${encodeURIComponent(next)}`);
  }

  return (
    <section aria-labelledby={titleId} className={styles.gate}>
      <div aria-hidden="true" className={styles.mark}>
        {minimumAge ? `${minimumAge}+` : ageAction ? "AGE" : "!"}
      </div>
      <p className={styles.eyebrow}>{ageAction ? "AGE-RESTRICTED ACCESS" : "ACCESS CHECK"}</p>
      <h2 id={titleId}>{ageAction ? `Verify your age to view ${title}` : `${title} is unavailable`}</h2>
      <p>{reason}</p>
      {ageAction && <p className={styles.separation}>Age assurance is separate from purchase, subscription, creator identity and payout checks. The server evaluates each requirement independently.</p>}

      {countries.length > 0 && canVerify && (
        <label className={styles.country}>
          <span>Verification country</span>
          <select
            aria-label="Verification country"
            onChange={(event) => setCountry(event.target.value)}
            value={country}
          >
            <option value="">Use account or request country</option>
            {countries.map((item) => <option key={item.code} value={item.code}>{item.name}</option>)}
          </select>
        </label>
      )}

      <div className={styles.actions}>
        {mode === "login" ? (
          <button disabled={working} onClick={login} type="button">Log in to verify</button>
        ) : canVerify ? (
          <button disabled={working} onClick={() => void verify()} type="button">
            {working ? "Opening verification…" : code === "AGE_VERIFICATION_EXPIRED" ? "Verify again" : "Verify age"}
          </button>
        ) : (
          <button disabled={working} onClick={() => void retry()} type="button">
            {working ? "Checking…" : "Check access again"}
          </button>
        )}
      </div>
      {action === "CONTACT_SUPPORT" && <p className={styles.support}>This state needs support or compliance review before access can continue.</p>}
      {error && <p className={styles.error} role="alert">{error}</p>}
    </section>
  );
}
