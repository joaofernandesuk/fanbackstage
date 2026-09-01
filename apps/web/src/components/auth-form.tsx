"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useId, useRef, useState } from "react";

import { ApiError, api } from "../lib/api";
import {
  authEntryPath,
  authErrorMessage,
  authSuccessPath,
  type AuthMode,
  DEFAULT_LOGIN_DESTINATION,
  DEFAULT_REGISTRATION_DESTINATION,
  rememberRegistrationReturn,
  safeAuthReturnPath,
} from "../lib/auth-ui";
import {
  type ComplianceCountry,
  type ComplianceDecision,
  getComplianceCountries,
  getComplianceDecision,
  safeProviderAuthorizationUrl,
  startAgeVerification,
} from "../lib/compliance-api";
import type { LegalDocument } from "../lib/legal";
import { legalDocumentPath } from "../lib/legal";
import styles from "./auth-form.module.css";

const REGISTRATION_COUNTRY_KEY = "fanbackstage.registration.country";

export function AuthForm({
  mode,
  presentation = "page",
  nextPath,
  onModeChange,
  onSuccess,
}: {
  mode: AuthMode;
  presentation?: "page" | "dialog";
  nextPath?: string;
  onModeChange?: (mode: AuthMode) => void;
  onSuccess?: (destination: string) => void;
}) {
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [resending, setResending] = useState(false);
  const [resendStatus, setResendStatus] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [unverifiedEmail, setUnverifiedEmail] = useState<string | null>(null);
  const [countries, setCountries] = useState<ComplianceCountry[]>([]);
  const [countryCode, setCountryCode] = useState("");
  const [registrationDecision, setRegistrationDecision] = useState<ComplianceDecision | null>(null);
  const [legalDocuments, setLegalDocuments] = useState<LegalDocument[]>([]);
  const [acceptedLegal, setAcceptedLegal] = useState<Set<string>>(new Set());
  const [registrationSetupPending, setRegistrationSetupPending] = useState(false);
  const [registrationSetupError, setRegistrationSetupError] = useState("");
  const [verificationPending, setVerificationPending] = useState(false);
  const selectedCountryRef = useRef("");
  const router = useRouter();
  const formId = useId();
  const emailId = `${formId}-email`;
  const passwordId = `${formId}-password`;
  const passwordHintId = `${formId}-password-hint`;
  const adultConfirmationId = `${formId}-adult-confirmation`;
  const errorId = `${formId}-error`;
  const titleId = `${formId}-title`;
  const isLogin = mode === "login";
  const passwordDescription = [!isLogin ? passwordHintId : null, error ? errorId : null]
    .filter(Boolean)
    .join(" ") || undefined;
  const returnPath = safeAuthReturnPath(
    nextPath,
    isLogin ? DEFAULT_LOGIN_DESTINATION : DEFAULT_REGISTRATION_DESTINATION,
  );

  useEffect(() => {
    if (isLogin) return;
    let active = true;
    setRegistrationSetupPending(true);
    Promise.all([
      getComplianceCountries(),
      getComplianceDecision("new_fan_registration", false),
    ]).then(([availableCountries, decision]) => {
      if (!active) return;
      setCountries(availableCountries);
      // A user selection can complete its country-specific request before this
      // initial fallback request. Never let the stale fallback overwrite it.
      if (selectedCountryRef.current) return;
      setRegistrationDecision(decision);
      const rememberedCountry = typeof window !== "undefined"
        ? window.sessionStorage.getItem(REGISTRATION_COUNTRY_KEY)
        : null;
      if (
        rememberedCountry
        && availableCountries.some((item) => item.code === rememberedCountry)
      ) {
        selectedCountryRef.current = rememberedCountry;
        setCountryCode(rememberedCountry);
      } else if (
        decision.jurisdiction
        && availableCountries.some((item) => item.code === decision.jurisdiction)
      ) {
        selectedCountryRef.current = decision.jurisdiction;
        setCountryCode(decision.jurisdiction);
      }
      setRegistrationSetupError("");
    }).catch((caught: unknown) => {
      if (!active) return;
      setRegistrationSetupError(
        caught instanceof ApiError
          ? caught.message
          : "Registration policy is temporarily unavailable.",
      );
    }).finally(() => {
      if (active) setRegistrationSetupPending(false);
    });
    return () => {
      active = false;
    };
  }, [isLogin]);

  useEffect(() => {
    if (isLogin || !countryCode) {
      setLegalDocuments([]);
      setAcceptedLegal(new Set());
      return;
    }
    let active = true;
    setRegistrationSetupPending(true);
    Promise.all([
      getComplianceDecision("new_fan_registration", false, countryCode),
      api<{ documents: LegalDocument[] }>(
        `/legal/registration-requirements?jurisdiction_code=${encodeURIComponent(countryCode)}`,
      ),
    ]).then(([decision, requirements]) => {
      if (!active) return;
      setRegistrationDecision(decision);
      setLegalDocuments(requirements.documents);
      setAcceptedLegal(new Set());
      setRegistrationSetupError("");
    }).catch((caught: unknown) => {
      if (!active) return;
      setRegistrationDecision(null);
      setLegalDocuments([]);
      setAcceptedLegal(new Set());
      setRegistrationSetupError(
        caught instanceof ApiError
          ? caught.message
          : "Registration requirements could not be confirmed.",
      );
    }).finally(() => {
      if (active) setRegistrationSetupPending(false);
    });
    return () => {
      active = false;
    };
  }, [countryCode, isLogin]);

  async function beginRegistrationVerification() {
    if (!countryCode) {
      setRegistrationSetupError("Choose your country before starting verification.");
      return;
    }
    setVerificationPending(true);
    setRegistrationSetupError("");
    try {
      window.sessionStorage.setItem(REGISTRATION_COUNTRY_KEY, countryCode);
      const started = await startAgeVerification({
        countryCode,
        returnPath: authEntryPath("register", returnPath),
      });
      const destination = safeProviderAuthorizationUrl(started.authorization_url);
      if (!destination) throw new Error("The verification provider returned an unsafe redirect.");
      window.location.assign(destination);
    } catch (caught) {
      setRegistrationSetupError(
        caught instanceof ApiError
          ? caught.message
          : caught instanceof Error
            ? caught.message
            : "Age verification could not be started.",
      );
      setVerificationPending(false);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setResendStatus("");
    setUnverifiedEmail(null);
    if (!isLogin) {
      if (!countryCode || !registrationDecision?.allowed) {
        setError("Complete the registration access check before creating an account.");
        return;
      }
      if (acceptedLegal.size !== legalDocuments.length) {
        setError("Review and accept each required legal document before continuing.");
        return;
      }
    }
    setPending(true);
    const data = new FormData(event.currentTarget);
    const submittedEmail = String(data.get("email") || "").trim();
    try {
      await api(`/auth/${mode}`, {
        method: "POST",
        body: JSON.stringify({
          email: data.get("email"),
          password: data.get("password"),
          ...(!isLogin && data.get("adult_confirmed") === "true"
            ? {
                adult_confirmed: true,
                country_code: countryCode,
                legal_version_ids: [...acceptedLegal],
              }
            : {}),
        }),
      });
      if (!isLogin && typeof window !== "undefined") {
        rememberRegistrationReturn(window.localStorage, returnPath);
        window.sessionStorage.removeItem(REGISTRATION_COUNTRY_KEY);
      }
      const destination = authSuccessPath(mode, returnPath);
      if (onSuccess) onSuccess(destination);
      else {
        router.push(destination);
        router.refresh();
      }
    } catch (caught) {
      if (
        isLogin &&
        caught instanceof ApiError &&
        caught.status === 403 &&
        caught.message === "Verify your email address before logging in."
      ) {
        setUnverifiedEmail(submittedEmail);
      }
      setError(caught instanceof ApiError
        ? !isLogin && caught.code === "JURISDICTION_UNRESOLVED"
          ? "Choose your country, complete its registration check, and try again."
          : authErrorMessage(mode, caught.status, caught.message)
        : "FanBackstage could not complete this request. Try again shortly.");
    } finally {
      setPending(false);
    }
  }

  async function resendVerification() {
    if (!unverifiedEmail) return;
    setResending(true);
    setResendStatus("");
    try {
      await api("/auth/resend-verification", {
        method: "POST",
        body: JSON.stringify({ email: unverifiedEmail }),
      });
      setResendStatus("If the account needs verification, a new link has been sent.");
    } catch (caught) {
      setResendStatus(
        caught instanceof ApiError && caught.status === 429
          ? "Too many attempts. Wait a moment, then try again."
          : "The verification email could not be requested. Try again shortly.",
      );
    } finally {
      setResending(false);
    }
  }

  const form = (
    <div className={presentation === "dialog" ? styles.dialogForm : styles.formPanel}>
      <div className={styles.formHeading}>
        <p>{isLogin ? "Welcome backstage" : "Join FanBackstage"}</p>
        <h1 id={titleId}>{isLogin ? "Welcome back" : "Create your account"}</h1>
        <span>
          {isLogin
            ? "Pick up where you left off with your creators, messages, and unlocked experiences."
            : "Start free, discover creators, and choose exactly whose world you want to join."}
        </span>
      </div>

      <form aria-labelledby={titleId} className={styles.form} onSubmit={submit}>
        {!isLogin && (
          <div className={styles.registrationPolicy}>
            <label htmlFor={`${formId}-country`}>
              Country or jurisdiction
              <select
                disabled={pending || registrationSetupPending || verificationPending}
                id={`${formId}-country`}
                onChange={(event) => {
                  selectedCountryRef.current = event.target.value;
                  setCountryCode(event.target.value);
                }}
                required
                value={countryCode}
              >
                <option value="">Choose your country</option>
                {countries.map((country) => (
                  <option key={country.code} value={country.code}>{country.name}</option>
                ))}
              </select>
            </label>
            {registrationSetupPending ? <p role="status">Checking registration requirements…</p> : null}
            {registrationSetupError ? <p className={styles.error} role="alert">{registrationSetupError}</p> : null}
            {registrationDecision && !registrationDecision.allowed ? (
              <div className={styles.policyNotice}>
                <strong>{registrationDecision.reason}</strong>
                <span>
                  {registrationDecision.action === "VERIFY_AGE"
                    ? `Policy requires ${registrationDecision.required_minimum_age ?? 18}+ age assurance before registration.`
                    : "Registration remains blocked until the server confirms the applicable policy."}
                </span>
                {registrationDecision.action === "VERIFY_AGE" ? (
                  <button
                    disabled={verificationPending || registrationSetupPending}
                    onClick={() => void beginRegistrationVerification()}
                    type="button"
                  >
                    {verificationPending ? "Opening verification…" : "Verify age to continue"}
                  </button>
                ) : null}
              </div>
            ) : registrationDecision?.allowed ? (
              <p className={styles.policyAllowed} role="status">
                Registration access confirmed for {registrationDecision.jurisdiction}.
              </p>
            ) : null}
          </div>
        )}

        <label htmlFor={emailId}>
          Email address
          <input
            autoCapitalize="none"
            autoComplete="email"
            autoFocus={presentation === "dialog"}
            disabled={pending}
            aria-describedby={error ? errorId : undefined}
            aria-invalid={Boolean(error)}
            id={emailId}
            inputMode="email"
            name="email"
            placeholder="you@example.com"
            required
            spellCheck={false}
            type="email"
          />
        </label>
        <div className={styles.passwordControl}>
          <div className={styles.passwordLabel}>
            <label htmlFor={passwordId}>Password</label>
            {isLogin && <Link href="/forgot-password">Forgot password?</Link>}
          </div>
          <div className={styles.passwordField}>
            <input
              autoComplete={isLogin ? "current-password" : "new-password"}
              aria-describedby={passwordDescription}
              aria-invalid={Boolean(error)}
              disabled={pending}
              id={passwordId}
              minLength={12}
              name="password"
              required
              type={showPassword ? "text" : "password"}
            />
            <button
              aria-label={showPassword ? "Hide password" : "Show password"}
              aria-pressed={showPassword}
              className={styles.passwordToggle}
              onClick={() => setShowPassword((visible) => !visible)}
              type="button"
            >
              {showPassword ? "Hide" : "Show"}
            </button>
          </div>
          {!isLogin && <small id={passwordHintId}>Use at least 12 characters.</small>}
        </div>

        {!isLogin && (
          <label className={styles.adultConfirmation} htmlFor={adultConfirmationId}>
            <input
              aria-describedby={error ? errorId : undefined}
              aria-invalid={Boolean(error)}
              disabled={pending}
              id={adultConfirmationId}
              name="adult_confirmed"
              required
              type="checkbox"
              value="true"
            />
            <span>I confirm I am at least 18 years old.</span>
          </label>
        )}

        {!isLogin && legalDocuments.length > 0 ? (
          <fieldset className={styles.legalAcceptances}>
            <legend>Required legal documents</legend>
            {legalDocuments.map((document) => (
              <label key={document.version_id}>
                <input
                  checked={acceptedLegal.has(document.version_id)}
                  disabled={pending}
                  onChange={(event) => setAcceptedLegal((current) => {
                    const next = new Set(current);
                    if (event.target.checked) next.add(document.version_id);
                    else next.delete(document.version_id);
                    return next;
                  })}
                  type="checkbox"
                />
                <span>
                  I accept{" "}
                  <Link href={legalDocumentPath(document.slug, countryCode)} target="_blank">
                    {document.title} (version {document.version})
                  </Link>.
                </span>
              </label>
            ))}
          </fieldset>
        ) : null}

        {error && <p className={styles.error} id={errorId} role="alert">{error}</p>}
        {unverifiedEmail && (
          <div className={styles.verificationHelp}>
            <button
              className={styles.textButton}
              disabled={resending}
              onClick={() => void resendVerification()}
              type="button"
            >
              {resending ? "Requesting…" : "Resend verification email"}
            </button>
            {resendStatus && <p role="status">{resendStatus}</p>}
          </div>
        )}

        <button
          className={styles.submit}
          disabled={
            pending
            || (!isLogin && (
              registrationSetupPending
              || !registrationDecision?.allowed
              || !countryCode
              || acceptedLegal.size !== legalDocuments.length
            ))
          }
          type="submit"
        >
          {pending ? "Please wait…" : isLogin ? "Log in" : "Create account"}
        </button>
      </form>

      <p className={styles.switchMode}>
        {isLogin ? "New to FanBackstage?" : "Already have an account?"}{" "}
        {onModeChange ? (
          <button className={styles.textButton} onClick={() => onModeChange(isLogin ? "register" : "login")} type="button">
            {isLogin ? "Join free" : "Log in"}
          </button>
        ) : (
          <Link href={authEntryPath(isLogin ? "register" : "login", returnPath)}>
            {isLogin ? "Join free" : "Log in"}
          </Link>
        )}
      </p>
    </div>
  );

  if (presentation === "dialog") return form;

  return (
    <section aria-labelledby={titleId} className={styles.pageShell}>
      <aside aria-hidden="true" className={styles.brandPanel}>
        <div className={styles.brandMark}>
          <Image alt="" height={156} src="/brand/fanbackstage_wordmark_transparent.png" width={707} />
        </div>
        <p>GET CLOSER. GO BACKSTAGE.</p>
        <h2>{isLogin ? "Your creator world is waiting." : "One account. Every backstage moment."}</h2>
        <ul>
          <li>Discover public creator worlds</li>
          <li>Follow, subscribe, and unlock on your terms</li>
          <li>Access is always resolved securely</li>
        </ul>
      </aside>
      {form}
    </section>
  );
}
