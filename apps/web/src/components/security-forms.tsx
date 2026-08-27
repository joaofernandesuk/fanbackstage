"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { FormEvent, useState } from "react";

import { api, ApiError } from "../lib/api";
import {
  authEntryPath,
  clearRegistrationReturn,
  registrationReturn,
  safeAuthReturnPath,
} from "../lib/auth-ui";

export function ForgotPasswordForm() {
  const [message, setMessage] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const response = await api<{ message: string }>("/auth/forgot-password", {
        method: "POST",
        body: JSON.stringify({ email: form.get("email") }),
      });
      setMessage(response.message);
    } catch (caught) {
      setMessage(caught instanceof ApiError ? caught.message : "Unable to continue");
    }
  }

  return (
    <section className="card">
      <h1>Reset your password</h1>
      <form onSubmit={submit}>
        <label>Email<input required name="email" type="email" /></label>
        <button>Send reset link</button>
      </form>
      {message && <p role="status">{message}</p>}
    </section>
  );
}

export function TokenForm({ kind }: { kind: "verify-email" | "reset-password" }) {
  const params = useSearchParams();
  const [message, setMessage] = useState("");
  const [loginPath, setLoginPath] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const token = String(form.get("token") || params.get("token") || "");
    try {
      const response = await api<{ message: string }>(`/auth/${kind}`, {
        method: "POST",
        body: JSON.stringify(kind === "reset-password"
          ? { token, new_password: form.get("password") }
          : { token }),
      });
      setMessage(response.message);
      if (kind === "verify-email") {
        const next = typeof window === "undefined"
          ? safeAuthReturnPath(params.get("next"), "/welcome")
          : registrationReturn(window.localStorage, params.get("next"));
        if (typeof window !== "undefined") clearRegistrationReturn(window.localStorage);
        setLoginPath(authEntryPath("login", next));
      }
    } catch (caught) {
      setMessage(caught instanceof ApiError ? caught.message : "Unable to continue");
    }
  }

  const verifying = kind === "verify-email";
  return (
    <section className="card">
      <h1>{verifying ? "Verify your email" : "Choose a new password"}</h1>
      <form onSubmit={submit}>
        <label>
          Token
          <input required name="token" defaultValue={params.get("token") ?? ""} />
        </label>
        {!verifying && (
          <label>
            New password
            <input required minLength={12} name="password" type="password" />
          </label>
        )}
        <button>{verifying ? "Verify email" : "Reset password"}</button>
      </form>
      {message && <p role="status">{message}</p>}
      {loginPath && (
        <p>
          <Link className="button" href={loginPath}>Log in to continue</Link>
        </p>
      )}
    </section>
  );
}
