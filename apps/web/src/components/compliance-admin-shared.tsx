import type { ReactNode } from "react";

import { ApiError } from "../lib/api";
import { pageSummary } from "../lib/compliance-admin";
import styles from "./compliance-admin.module.css";

export function operationalError(caught: unknown, fallback: string): string {
  if (caught instanceof ApiError && caught.status < 500) return caught.message;
  if (caught instanceof Error && !(caught instanceof ApiError)) return caught.message;
  return fallback;
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "Not set";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "Invalid timestamp";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(parsed);
}

export function SectionHeader({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow: string;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <header className={styles.sectionHeader}>
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h2>{title}</h2>
        <p>{description}</p>
      </div>
      {action}
    </header>
  );
}

export function StatusBadge({ value, label }: { value: string | boolean; label?: string }) {
  const normalized = String(value).toLowerCase();
  const good = value === true || ["active", "verified", "healthy", "enabled", "allowed", "processed"].includes(normalized);
  const bad = value === false || ["failed", "revoked", "unavailable", "misconfigured", "blocked", "denied", "rejected"].includes(normalized);
  const warning = ["pending", "scheduled", "review_required", "degraded", "expired", "retired", "draft"].includes(normalized);
  const tone = good ? styles.badgeGood : bad ? styles.badgeBad : warning ? styles.badgeWarn : "";
  return <span className={`${styles.badge} ${tone}`}>{label ?? normalized.replaceAll("_", " ")}</span>;
}

export function LoadingState({ children = "Loading operational data…" }: { children?: ReactNode }) {
  return <div className={styles.loadingState} role="status">{children}</div>;
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className={styles.emptyState}>{children}</div>;
}

export function FormMessage({ message, error }: { message: string; error?: boolean }) {
  if (!message) return null;
  return <div className={error ? styles.errorState : styles.notice} role={error ? "alert" : "status"}>{message}</div>;
}

export function Pagination({
  page,
  pageSize,
  total,
  onPage,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPage: (page: number) => void;
}) {
  const hasPrevious = page > 1;
  const hasNext = page * pageSize < total;
  return (
    <div className={styles.pagination}>
      <p>{pageSummary(page, pageSize, total)}</p>
      <div className={styles.paginationActions}>
        <button className={styles.ghostButton} disabled={!hasPrevious} onClick={() => onPage(page - 1)} type="button">Previous</button>
        <button className={styles.ghostButton} disabled={!hasNext} onClick={() => onPage(page + 1)} type="button">Next</button>
      </div>
    </div>
  );
}
