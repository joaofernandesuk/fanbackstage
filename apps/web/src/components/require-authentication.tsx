"use client";

import { type ReactNode, useEffect, useState } from "react";
import { usePathname } from "next/navigation";

import { ApiError, api, type CurrentUser } from "../lib/api";
import { authEntryPath } from "../lib/auth-ui";

type AuthenticationState = "checking" | "authenticated" | "unavailable";

const PROTECTED_ROUTE_PREFIXES = [
  "/account",
  "/admin",
  "/appeals",
  "/creator-onboarding",
  "/creator-studio",
  "/groups",
  "/marketplace/orders",
  "/messages",
  "/moderation",
  "/notification-settings",
  "/notifications",
  "/purchases",
  "/referrals",
  "/subscriptions",
];

export function isProtectedRoute(pathname: string): boolean {
  return PROTECTED_ROUTE_PREFIXES.some((prefix) => (
    pathname === prefix || pathname.startsWith(`${prefix}/`)
  ));
}

/**
 * Keeps protected client workspaces from rendering their controls until the
 * session has been confirmed by the API. The API remains the authority for
 * every operation; this only gives signed-out people the expected route back
 * to Login instead of presenting a page full of failed requests.
 */
export function RequireAuthentication({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const protectedRoute = isProtectedRoute(pathname);
  const [state, setState] = useState<AuthenticationState>("checking");
  const [checkedPathname, setCheckedPathname] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    if (!protectedRoute) {
      setState("authenticated");
      setCheckedPathname(pathname);
      return () => {
        active = false;
      };
    }

    setState("checking");
    setCheckedPathname(null);

    void api<CurrentUser>("/me")
      .then(() => {
        if (active) {
          setState("authenticated");
          setCheckedPathname(pathname);
        }
      })
      .catch((error: unknown) => {
        if (!active) return;
        if (error instanceof ApiError && error.status === 401) {
          const currentPath = `${window.location.pathname}${window.location.search}${window.location.hash}`;
          window.location.replace(authEntryPath("login", currentPath));
          return;
        }
        setState("unavailable");
        setCheckedPathname(pathname);
      });

    return () => {
      active = false;
    };
  }, [pathname, protectedRoute]);

  if (!protectedRoute || (state === "authenticated" && checkedPathname === pathname)) {
    return <>{children}</>;
  }

  return (
    <section aria-busy={state === "checking"} className="card" role={state === "unavailable" ? "alert" : "status"}>
      <h1>{state === "checking" ? "Checking your session" : "Unable to confirm your session"}</h1>
      <p>{state === "checking" ? "Taking you to sign in if needed." : "Please refresh the page and try again."}</p>
    </section>
  );
}
