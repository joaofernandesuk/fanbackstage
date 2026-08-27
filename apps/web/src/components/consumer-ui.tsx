"use client";

import Image from "next/image";
import Link from "next/link";
import { ReactNode, useCallback, useEffect, useState } from "react";

import { CurrentUser, api } from "../lib/api";
import { authEntryPath, AuthMode } from "../lib/auth-ui";
import { accessLabel } from "../lib/public-api";
import { mediaForUsername } from "../lib/demo-personas";
import { useAuthExperience } from "./auth-experience";
import styles from "./consumer-ui.module.css";

export { mediaForUsername } from "../lib/demo-personas";

let currentUserRequest: Promise<CurrentUser | null> | null = null;

function loadCurrentUser() {
  if (!currentUserRequest) {
    const request = api<CurrentUser>("/me").catch(() => null);
    currentUserRequest = request;
    void request.finally(() => {
      if (currentUserRequest === request) currentUserRequest = null;
    });
  }
  return currentUserRequest;
}

export function useLoginGate() {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(true);
  const { openAuth } = useAuthExperience();

  useEffect(() => {
    let active = true;
    loadCurrentUser()
      .then((current) => {
        if (active) setUser(current);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const requireLogin = useCallback((request?: { mode?: AuthMode; nextPath?: string }) => {
    if (user) return true;
    openAuth(request?.mode ?? "login", request?.nextPath);
    return false;
  }, [openAuth, user]);

  return { authenticated: Boolean(user), loading, requireLogin, user } as const;
}

export function LoginGate({
  children,
  className,
  label = "Log in to continue",
  nextPath,
}: {
  children: ReactNode;
  className?: string;
  label?: string;
  nextPath?: string;
}) {
  const { authenticated, loading, requireLogin } = useLoginGate();
  if (loading) return <span className={className} aria-busy="true">Checking access…</span>;
  if (!authenticated) {
    return (
      <Link
        aria-haspopup="dialog"
        className={className}
        href={authEntryPath("login", nextPath)}
        onNavigate={(event) => {
          event.preventDefault();
          requireLogin({ nextPath });
        }}
      >
        {label}
      </Link>
    );
  }
  return <>{children}</>;
}

export function CreatorAvatar({
  username,
  displayName,
  size = 48,
  className = "",
  live = false,
}: {
  username?: string | null;
  displayName: string;
  size?: number;
  className?: string;
  live?: boolean;
}) {
  const source = mediaForUsername(username)?.avatar;
  return (
    <span
      className={`${styles.avatar} ${live ? styles.avatarLive : ""} ${className}`}
      style={{ width: size, height: size }}
    >
      {source ? (
        <Image alt={`${displayName} profile photo`} height={size} src={source} width={size} />
      ) : (
        <span aria-label={`${displayName} profile placeholder`} className={styles.initials}>
          {displayName.slice(0, 2).toUpperCase()}
        </span>
      )}
    </span>
  );
}

export function AccessBadge({ policy, locked = false }: { policy?: string | null; locked?: boolean }) {
  const label = accessLabel(policy, locked);
  const tone = policy === "ppv" ? styles.badgePremium : policy === "subscription" || policy === "subscribers" ? styles.badgeSubscriber : styles.badgeFree;
  return <span className={`${styles.badge} ${tone}`}>{label}</span>;
}

export function VerifiedBadge({ label = "Verified" }: { label?: string }) {
  return <span aria-label={label} className={styles.verified} title={label}>✓</span>;
}

export function EmptyState({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action?: ReactNode;
}) {
  return (
    <div className={styles.empty} role="status">
      <span aria-hidden="true" className={styles.emptyMark}>FB</span>
      <h3>{title}</h3>
      <p>{body}</p>
      {action}
    </div>
  );
}

export function Skeleton({ label = "Loading content", lines = 3 }: { label?: string; lines?: number }) {
  return (
    <div aria-label={label} aria-live="polite" aria-busy="true" className={styles.skeleton}>
      <span className={styles.skeletonMedia} />
      {Array.from({ length: lines }, (_, index) => <span key={index} className={styles.skeletonLine} />)}
    </div>
  );
}

export function SectionHeader({
  eyebrow,
  title,
  body,
  href,
  linkLabel = "View all",
  id,
}: {
  eyebrow?: string;
  title: string;
  body?: string;
  href?: string;
  linkLabel?: string;
  id?: string;
}) {
  return (
    <div className={styles.sectionHeader}>
      <div>
        {eyebrow && <p className={styles.eyebrow}>{eyebrow}</p>}
        <h2 id={id}>{title}</h2>
        {body && <p>{body}</p>}
      </div>
      {href && <Link className={styles.sectionLink} href={href}>{linkLabel}<span aria-hidden="true"> →</span></Link>}
    </div>
  );
}
