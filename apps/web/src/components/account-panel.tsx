"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError, CurrentUser } from "../lib/api";

export function AccountPanel() {
  const [user, setUser] = useState<CurrentUser | null>(null); const [error, setError] = useState(""); const router = useRouter();
  useEffect(() => { api<CurrentUser>("/me").then(setUser).catch((e: unknown) => { setError(e instanceof ApiError ? e.message : "Unable to load account"); }); }, []);
  async function logout() { await api("/auth/logout", { method: "POST" }); router.push("/login"); }
  if (error) return <section className="card"><h1>Account</h1><p role="alert" className="error">{error}</p></section>;
  if (!user) return <section className="card"><p>Loading your FanBackstage account…</p></section>;
  return <section className="card"><p className="eyebrow">ACCOUNT</p><h1>Your FanBackstage</h1><p>{user.email}</p><p>Roles: {user.roles.join(", ")}</p><button onClick={logout}>Log out</button></section>;
}
