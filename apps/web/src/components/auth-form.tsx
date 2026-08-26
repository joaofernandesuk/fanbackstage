"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useId, useState } from "react";

import { ApiError, api } from "../lib/api";
import { AuthMode, authSuccessPath } from "../lib/auth-ui";
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
  const [showPassword, setShowPassword] = useState(false);
  const router = useRouter();
  const formId = useId();
  const emailId = `${formId}-email`;
  const passwordId = `${formId}-password`;
  const titleId = `${formId}-title`;
  const isLogin = mode === "login";

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setPending(true);
    const data = new FormData(event.currentTarget);
    try {
      await api(`/auth/${mode}`, {
        method: "POST",
        body: JSON.stringify({ email: data.get("email"), password: data.get("password") }),
      });
      const queryNext = typeof window === "undefined"
        ? undefined
        : new URLSearchParams(window.location.search).get("next");
      const destination = authSuccessPath(mode, nextPath ?? queryNext);
      if (onSuccess) onSuccess(destination);
      else {
        router.push(destination);
        router.refresh();
      }
    } catch (caught) {
      const message = caught instanceof ApiError ? caught.message : "Unable to continue";
      setError(typeof message === "string" ? message : "Please check the form fields and try again.");
    } finally {
      setPending(false);
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
            id={emailId}
            inputMode="email"
            name="email"
            placeholder="you@example.com"
            required
            spellCheck={false}
            type="email"
          />
        </label>
        <label htmlFor={passwordId}>
          <span className={styles.passwordLabel}>
            Password
            {isLogin && <Link href="/forgot-password">Forgot password?</Link>}
          </span>
          <span className={styles.passwordField}>
            <input
              autoComplete={isLogin ? "current-password" : "new-password"}
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
          </span>
          {!isLogin && <small>Use at least 12 characters.</small>}
        </label>

        {error && <p className={styles.error} role="alert">{error}</p>}

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
          <Link href={isLogin ? "/register" : "/login"}>{isLogin ? "Join free" : "Log in"}</Link>
        )}
      </p>

      {!isLogin && (
        <p className={styles.terms}>
          By creating an account, you confirm that you are at least 18 and agree to follow FanBackstage’s platform rules.
        </p>
      )}
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
