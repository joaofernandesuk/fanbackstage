"use client";
import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "../lib/api";

export function AuthForm({ mode }: { mode: "login" | "register" }) {
  const [error, setError] = useState(""); const router = useRouter();
  async function submit(e: FormEvent<HTMLFormElement>) { e.preventDefault(); setError(""); const data = new FormData(e.currentTarget);
    try { await api(`/auth/${mode}`, { method: "POST", body: JSON.stringify({ email: data.get("email"), password: data.get("password") }) }); router.push(mode === "login" ? "/account" : "/verify-email"); }
    catch (error) { const message = error instanceof ApiError ? error.message : "Unable to continue"; setError(typeof message === "string" ? message : "Please check the form fields and try again."); }
  }
  return <section className="card"><h1>{mode === "login" ? "Welcome back" : "Create your account"}</h1><form onSubmit={submit}><label>Email<input required name="email" type="email" autoComplete="email" /></label><label>Password<input required minLength={12} name="password" type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} /></label>{error && <p role="alert" className="error">{error}</p>}<button>{mode === "login" ? "Log in" : "Create account"}</button></form></section>;
}
