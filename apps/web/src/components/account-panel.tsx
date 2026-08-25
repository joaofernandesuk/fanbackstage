"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api, ApiError, CurrentUser } from "../lib/api";

export function AccountPanel() {
  const [user, setUser] = useState<CurrentUser | null>(null); const [error, setError] = useState(""); const router = useRouter();
  useEffect(() => { api<CurrentUser>("/me").then(setUser).catch((e: unknown) => { setError(e instanceof ApiError ? e.message : "Unable to load account"); }); }, []);
  async function logout() { await api("/auth/logout", { method: "POST" }); router.push("/login"); }
  if (error) return <section className="card"><h1>Account</h1><p role="alert" className="error">{error}</p></section>;
  if (!user) return <section className="card"><p>Loading your FanBackstage account…</p></section>;
  return <section className="card"><p className="eyebrow">ACCOUNT</p><h1>Your FanBackstage</h1><p>{user.email}</p><p>Roles: {user.roles.join(", ")}</p><Link className="button" href="/creator-onboarding">Become a creator</Link><Link className="button" href="/purchases">Purchase history</Link><Link className="button" href="/marketplace/orders">Marketplace orders</Link><Link className="button" href="/subscriptions">Subscriptions</Link><Link className="button" href="/notifications">Notifications</Link><Link className="button" href="/notification-settings">Notification settings</Link>{user.roles.includes("creator") && <Link className="button" href="/creator-studio">Creator studio</Link>}<button onClick={logout}>Log out</button></section>;
}
