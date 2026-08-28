"use client";

import { useCallback, useEffect, useState } from "react";

import { api } from "../lib/api";
import type {
  ComplianceAuditEvent,
  CountryPolicy,
  FeatureFlagRevision,
  PageResult,
  ProviderInventory,
  VerificationAttempt,
} from "../lib/compliance-admin";
import { ComplianceCountries, ComplianceFeatureFlags, ComplianceTemplates } from "./compliance-admin-policies";
import {
  ComplianceAudit,
  ComplianceProviders,
  ComplianceRelatedControls,
  ComplianceSimulator,
  ComplianceVerifications,
} from "./compliance-admin-operations";
import styles from "./compliance-admin.module.css";
import {
  FormMessage,
  LoadingState,
  SectionHeader,
  StatusBadge,
  formatDateTime,
  operationalError,
} from "./compliance-admin-shared";

const sections = [
  ["overview", "Overview"],
  ["countries", "Countries / Jurisdictions"],
  ["templates", "Policy Templates"],
  ["features", "Feature Flags"],
  ["verifications", "Age Verification"],
  ["providers", "Providers & Diagnostics"],
  ["simulator", "Policy Simulator"],
  ["related", "KYC, Performers & Consent"],
  ["audit", "Compliance Audit"],
] as const;
type ComplianceSection = (typeof sections)[number][0];

function isComplianceSection(value: string): value is ComplianceSection {
  return sections.some(([key]) => key === value);
}

export function ComplianceAdmin() {
  const [active, setActive] = useState<ComplianceSection>("overview");

  useEffect(() => {
    const syncHash = () => {
      const next = window.location.hash.slice(1);
      if (isComplianceSection(next)) setActive(next);
    };
    syncHash();
    window.addEventListener("hashchange", syncHash);
    return () => window.removeEventListener("hashchange", syncHash);
  }, []);

  function selectSection(section: ComplianceSection) {
    setActive(section);
    window.history.replaceState(null, "", `${window.location.pathname}#${section}`);
  }

  return (
    <div className={styles.shell}>
      <header className={styles.hero}>
        <div className={styles.heroTop}>
          <div>
            <p className="eyebrow">Trust operations</p>
            <h1>Compliance control room</h1>
            <p>Versioned jurisdiction policy, age-assurance operations, provider health, and immutable audit evidence in one operational workspace.</p>
          </div>
          <StatusBadge value="fail_closed" label="Fail-closed controls" />
        </div>
        <div className={styles.separationNote}>
          <span aria-hidden="true" className={styles.separationMark}>≠</span>
          <div>
            <strong>Separate authorities stay separate</strong>
            <p>Viewer age assurance does not establish creator identity/KYC, performer verification, consent/releases, authentication, or commercial entitlement.</p>
          </div>
        </div>
      </header>

      <div className={styles.workspace}>
        <nav aria-label="Compliance administration" className={styles.sidebar}>
          {sections.map(([key, label]) => (
            <button
              aria-current={active === key ? "page" : undefined}
              className={`${styles.navButton} ${active === key ? styles.navButtonActive : ""}`}
              key={key}
              onClick={() => selectSection(key)}
              type="button"
            >
              <span aria-hidden="true" className={styles.navDot} />
              {label}
            </button>
          ))}
        </nav>

        <div className={styles.main}>
          {active === "overview" ? <ComplianceOverview onNavigate={selectSection} /> : null}
          {active === "countries" ? <ComplianceCountries /> : null}
          {active === "templates" ? <ComplianceTemplates /> : null}
          {active === "features" ? <ComplianceFeatureFlags /> : null}
          {active === "verifications" ? <ComplianceVerifications /> : null}
          {active === "providers" ? <ComplianceProviders /> : null}
          {active === "simulator" ? <ComplianceSimulator /> : null}
          {active === "related" ? <ComplianceRelatedControls /> : null}
          {active === "audit" ? <ComplianceAudit /> : null}
        </div>
      </div>
    </div>
  );
}

type OverviewSnapshot = {
  countries: PageResult<CountryPolicy>;
  review: PageResult<VerificationAttempt>;
  failed: PageResult<VerificationAttempt>;
  providers: ProviderInventory[];
  flags: PageResult<FeatureFlagRevision>;
  audit: PageResult<ComplianceAuditEvent>;
};

function ComplianceOverview({ onNavigate }: { onNavigate: (section: ComplianceSection) => void }) {
  const [snapshot, setSnapshot] = useState<OverviewSnapshot | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const [countries, review, failed, providers, flags, audit] = await Promise.all([
        api<PageResult<CountryPolicy>>("/admin/compliance/countries?page=1&page_size=100"),
        api<PageResult<VerificationAttempt>>("/admin/compliance/verifications?status=review_required&page=1&page_size=1"),
        api<PageResult<VerificationAttempt>>("/admin/compliance/verifications?status=failed&page=1&page_size=1"),
        api<ProviderInventory[]>("/admin/compliance/providers"),
        api<PageResult<FeatureFlagRevision>>("/admin/compliance/feature-flags?page=1&page_size=10"),
        api<PageResult<ComplianceAuditEvent>>("/admin/compliance/audit?page=1&page_size=5"),
      ]);
      setSnapshot({ countries, review, failed, providers, flags, audit });
    } catch (caught) {
      setError(operationalError(caught, "Unable to load the compliance overview."));
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  if (!snapshot && !error) return <section className={styles.panel}><LoadingState /></section>;
  if (!snapshot) return (
    <section className={styles.panel}>
      <FormMessage error message={error} />
      <button className={styles.secondaryButton} onClick={() => void load()} type="button">Try again</button>
    </section>
  );

  const configuredProviders = snapshot.providers.filter((provider) => provider.configuration_complete).length;
  const missingPolicies = snapshot.countries.items.filter((country) => !country.effective_policy).length;

  return (
    <>
      <section className={styles.panel}>
        <SectionHeader
          action={<button className={styles.secondaryButton} onClick={() => void load()} type="button">Refresh snapshot</button>}
          description="Live totals from the same operational APIs used by each control below."
          eyebrow="Overview"
          title="Readiness at a glance"
        />
        <div className={styles.statGrid}>
          <button className={styles.stat} onClick={() => onNavigate("countries")} type="button">
            <span className={styles.statLabel}>Country registry</span>
            <span className={styles.statValue}>{snapshot.countries.total}</span>
            <small>{missingPolicies ? `${missingPolicies} shown without an effective policy` : "All shown countries have an effective policy"}</small>
          </button>
          <button className={styles.stat} onClick={() => onNavigate("verifications")} type="button">
            <span className={styles.statLabel}>Review required</span>
            <span className={styles.statValue}>{snapshot.review.total}</span>
            <small>Age-assurance attempts awaiting controlled review</small>
          </button>
          <button className={styles.stat} onClick={() => onNavigate("verifications")} type="button">
            <span className={styles.statLabel}>Failed attempts</span>
            <span className={styles.statValue}>{snapshot.failed.total}</span>
            <small>Failures remain denied unless a new valid result or bounded review applies</small>
          </button>
          <button className={styles.stat} onClick={() => onNavigate("providers")} type="button">
            <span className={styles.statLabel}>Providers configured</span>
            <span className={styles.statValue}>{configuredProviders}/{snapshot.providers.length}</span>
            <small>Configuration status only; credentials are never displayed</small>
          </button>
        </div>
        {snapshot.countries.total > snapshot.countries.items.length ? (
          <p className={styles.footerNote}>Policy coverage note: the overview evaluates the first 100 countries. Open Countries for bounded pagination and search.</p>
        ) : null}
      </section>

      <section className={styles.panel}>
        <SectionHeader
          description="Provider health is operational state. An outage never disables policy requirements."
          eyebrow="Providers"
          title="Age-assurance health"
        />
        <div className={styles.statusStrip}>
          {snapshot.providers.map((provider) => {
            const status = provider.latest_probe?.status ?? (provider.configuration_complete ? "not_probed" : "misconfigured");
            return (
              <button className={`${styles.statusItem} ${status === "healthy" ? styles.toneGood : status === "degraded" || status === "not_probed" ? styles.toneWarn : styles.toneBad}`} key={provider.provider} onClick={() => onNavigate("providers")} type="button">
                <span aria-hidden="true" className={styles.statusDot} />
                <span>
                  <strong>{provider.provider}{provider.selected ? " · selected" : ""}</strong>
                  <p>{status.replaceAll("_", " ")} · {provider.environment ?? "environment unavailable"}</p>
                </span>
              </button>
            );
          })}
        </div>
      </section>

      <section className={styles.panel}>
        <SectionHeader
          action={<button className={styles.ghostButton} onClick={() => onNavigate("features")} type="button">Open feature history</button>}
          description="Recent global and jurisdiction-scoped revisions. Each row is append-only history, not a mutable switch."
          eyebrow="Operational flags"
          title="Latest revisions"
        />
        {snapshot.flags.items.length ? (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead><tr><th>Feature</th><th>Scope</th><th>State</th><th>Effective</th><th>Reason</th></tr></thead>
              <tbody>
                {snapshot.flags.items.slice(0, 5).map((flag) => (
                  <tr key={flag.id}>
                    <td className={styles.primaryCell}>{flag.feature.replaceAll("_", " ")}</td>
                    <td>{flag.country_scope ?? "Global"}</td>
                    <td><StatusBadge value={flag.enabled} label={flag.enabled ? "Enabled" : "Disabled"} /></td>
                    <td>{formatDateTime(flag.effective_from)}</td>
                    <td>{flag.change_reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p className={styles.emptyState}>No feature-flag revisions recorded.</p>}
      </section>

      <section className={styles.panel}>
        <SectionHeader
          action={<button className={styles.ghostButton} onClick={() => onNavigate("audit")} type="button">Search audit</button>}
          description="Recent immutable compliance, legal, site-settings, performer, consent, and creator-verification events."
          eyebrow="Audit"
          title="Recent control activity"
        />
        {snapshot.audit.items.length ? (
          <ul className={styles.timeline}>
            {snapshot.audit.items.map((event) => (
              <li className={styles.timelineItem} key={event.id}>
                <div className={styles.rowBetween}>
                  <strong>{event.event_type}</strong>
                  <time dateTime={event.created_at}>{formatDateTime(event.created_at)}</time>
                </div>
                <p>Target: {event.target_type ?? "none"}{event.target_id ? ` · ${event.target_id}` : ""} · Actor: {event.actor_user_id ?? "system"}</p>
              </li>
            ))}
          </ul>
        ) : <p className={styles.emptyState}>No compliance audit events recorded.</p>}
      </section>
      <FormMessage error message={error} />
    </>
  );
}
