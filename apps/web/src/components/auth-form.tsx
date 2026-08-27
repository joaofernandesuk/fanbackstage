"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useId, useState } from "react";

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
import styles from "./auth-form.module.css";

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

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setResendStatus("");
    setUnverifiedEmail(null);
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
            ? { adult_confirmed: true }
            : {}),
        }),
      });
      if (!isLogin && typeof window !== "undefined") {
        rememberRegistrationReturn(window.localStorage, returnPath);
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
        ? authErrorMessage(mode, caught.status, caught.message)
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
            <span>I confirm I am at least 18 years old and agree to the Terms.</span>
          </label>
        )}

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

        <button className={styles.submit} disabled={pending} type="submit">
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
          <Image alt="" height={48} src="/brand/fanbackstage_symbol_transparent.png" width={70} />
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
