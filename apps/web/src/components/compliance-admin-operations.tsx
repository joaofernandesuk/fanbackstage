"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../lib/api";
import {
  assuranceLevels,
  complianceFeatures,
  confirmedCompliancePayload,
  formatOperationalLabel,
  isUuid,
  localDateTimeValue,
  safeAuditMetadata,
  verificationStatuses,
  type AssuranceLevel,
  type ComplianceAuditEvent,
  type ComplianceFeature,
  type PageResult,
  type ProviderInventory,
  type ProviderProbe,
  type SimulationDecision,
  type VerificationAttempt,
  type VerificationStatus,
} from "../lib/compliance-admin";
import styles from "./compliance-admin.module.css";
import {
  EmptyState,
  FormMessage,
  LoadingState,
  Pagination,
  SectionHeader,
  StatusBadge,
  formatDateTime,
  operationalError,
} from "./compliance-admin-shared";

const PAGE_SIZE = 25;

export function ComplianceVerifications() {
  const [data, setData] = useState<PageResult<VerificationAttempt> | null>(null);
  const [page, setPage] = useState(1);
  const [searchDraft, setSearchDraft] = useState("");
  const [search, setSearch] = useState("");
  const [country, setCountry] = useState("");
  const [provider, setProvider] = useState("");
  const [status, setStatus] = useState("");
  const [retryableOnly, setRetryableOnly] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
    if (search) params.set("search", search);
    if (country.trim()) params.set("country_code", country.trim().toUpperCase());
    if (provider.trim()) params.set("provider", provider.trim());
    if (status) params.set("status", status);
    try {
      setData(await api<PageResult<VerificationAttempt>>(`/admin/compliance/verifications?${params}`));
    } catch (caught) {
      setError(operationalError(caught, "Unable to load age-verification attempts."));
    }
  }, [country, page, provider, search, status]);

  useEffect(() => { void load(); }, [load]);

  const shownItems = useMemo(
    () => retryableOnly ? (data?.items ?? []).filter((attempt) => attempt.retryable) : (data?.items ?? []),
    [data?.items, retryableOnly],
  );
  const selected = data?.items.find((attempt) => attempt.id === selectedId) ?? null;

  function applyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSearch(searchDraft.trim());
    setPage(1);
  }

  return (
    <section className={styles.panel}>
      <SectionHeader
        description="Inspect privacy-minimised viewer age-assurance attempts. Creator identity/KYC and performer verification are intentionally not mixed into this queue."
        eyebrow="Age assurance"
        title="Verification attempts"
      />
      <FormMessage message={message} />
      <FormMessage error message={error} />
      <form className={styles.filters} onSubmit={applyFilters}>
        <label>Account email search<input onChange={(event) => setSearchDraft(event.target.value)} placeholder="Authenticated attempts" value={searchDraft} /></label>
        <label>Country<input maxLength={2} onChange={(event) => setCountry(event.target.value)} placeholder="Any" value={country} /></label>
        <label>Provider<input onChange={(event) => setProvider(event.target.value)} placeholder="Any" value={provider} /></label>
        <label>Status<select onChange={(event) => setStatus(event.target.value)} value={status}><option value="">All statuses</option>{verificationStatuses.map((value) => <option key={value} value={value}>{formatOperationalLabel(value)}</option>)}</select></label>
        <button className={styles.secondaryButton} type="submit">Apply filters</button>
        <label className={styles.checkboxRow}><input checked={retryableOnly} onChange={(event) => setRetryableOnly(event.target.checked)} type="checkbox" />Retryable only on this page</label>
      </form>
      <p className={styles.hint}>Search is by authenticated account email. Anonymous attempts remain searchable through country, provider, and status filters; no provider evidence or raw response is returned.</p>
      {!data && !error ? <LoadingState /> : null}
      {data ? (
        <>
          {shownItems.length ? (
            <div className={styles.reviewGrid}>
              {shownItems.map((attempt) => (
                <article className={`${styles.reviewCard} ${attempt.id === selectedId ? styles.reviewCardSelected : ""}`} key={attempt.id}>
                  <div className={styles.reviewHeader}>
                    <div><strong>{attempt.anonymous ? "Anonymous session" : "Authenticated account"}</strong><p>{attempt.id}</p></div>
                    <StatusBadge value={attempt.status} />
                  </div>
                  <div className={styles.reviewMeta}>
                    <span>Provider<strong>{attempt.provider}</strong></span>
                    <span>Jurisdiction<strong>{attempt.country_code}</strong></span>
                    <span>Required<strong>{attempt.required_minimum_age}+ · {formatOperationalLabel(attempt.required_assurance)}</strong></span>
                    <span>Achieved<strong>{attempt.achieved_minimum_age ?? "Not established"} · {formatOperationalLabel(attempt.achieved_assurance)}</strong></span>
                    <span>Initiated<strong>{formatDateTime(attempt.initiated_at)}</strong></span>
                    <span>Expiry<strong>{formatDateTime(attempt.expires_at)}</strong></span>
                    <span>Policy<strong>{attempt.applicable_policy_version ? `v${attempt.applicable_policy_version}` : "Not returned"}</strong></span>
                    <span>Retryable<strong>{attempt.retryable ? "Yes" : "No"}</strong></span>
                  </div>
                  <p>Account: {attempt.user_id ?? "Not linked"}</p>
                  {attempt.anonymous ? <p>Anonymous reference: {attempt.anonymous_session_id ?? "Not returned by API"}</p> : null}
                  {attempt.failure_reason_code ? <p>Failure category: {attempt.failure_reason_code}</p> : null}
                  <p>Verified: {formatDateTime(attempt.verified_at)} · Failed: {formatDateTime(attempt.failed_at)} · Revoked: {formatDateTime(attempt.revoked_at)}</p>
                  <button className={styles.smallButton} onClick={() => setSelectedId(attempt.id === selectedId ? null : attempt.id)} type="button">{attempt.id === selectedId ? "Close review" : "Controlled review"}</button>
                </article>
              ))}
            </div>
          ) : <EmptyState>{retryableOnly && data.items.length ? "No retryable attempts on this bounded page." : "No verification attempts match these filters."}</EmptyState>}
          <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />
        </>
      ) : null}
      {selected ? (
        <VerificationReview
          attempt={selected}
          onComplete={async (notice) => {
            setMessage(notice);
            setSelectedId(null);
            await load();
          }}
        />
      ) : null}
    </section>
  );
}

function VerificationReview({
  attempt,
  onComplete,
}: {
  attempt: VerificationAttempt;
  onComplete: (message: string) => void | Promise<void>;
}) {
  const [status, setStatus] = useState<Exclude<VerificationStatus, "pending" | "expired">>("review_required");
  const [assurance, setAssurance] = useState<AssuranceLevel>(attempt.achieved_assurance);
  const [minimumAge, setMinimumAge] = useState(attempt.achieved_minimum_age ? String(attempt.achieved_minimum_age) : "");
  const [expiresAt, setExpiresAt] = useState(localDateTimeValue(attempt.expires_at));
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState("");
  const verifying = status === "verified";
  const verificationComplete = !verifying || (assurance !== "none" && minimumAge !== "" && expiresAt !== "");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!confirmed || reason.trim().length < 8 || !verificationComplete) return;
    setError("");
    try {
      const result = await api<{ id: string; status: string }>(`/admin/compliance/verifications/${attempt.id}/review`, {
        method: "POST",
        body: JSON.stringify(confirmedCompliancePayload({
          status,
          achieved_assurance_level: verifying ? assurance : null,
          achieved_minimum_age: verifying ? Number(minimumAge) : null,
          expires_at: verifying ? new Date(expiresAt).toISOString() : null,
        }, reason)),
      });
      await onComplete(`Verification ${result.id} is now ${result.status}.`);
    } catch (caught) {
      setError(operationalError(caught, "Unable to record the controlled review."));
    }
  }

  return (
    <form className={styles.safeChange} onSubmit={submit}>
      <h3>Controlled review · {attempt.id}</h3>
      <p>Manual review does not edit provider evidence. A verified outcome requires explicit achieved assurance, achieved minimum age, and a finite expiry.</p>
      <FormMessage error message={error} />
      <div className={styles.formGrid}>
        <label>Review outcome
          <select onChange={(event) => setStatus(event.target.value as typeof status)} value={status}>
            <option value="review_required">Keep in review</option>
            <option value="failed">Fail</option>
            <option value="revoked">Revoke</option>
            <option value="verified">Bounded manual verification</option>
          </select>
        </label>
        {verifying ? <>
          <label>Achieved assurance<select onChange={(event) => setAssurance(event.target.value as AssuranceLevel)} value={assurance}>{assuranceLevels.filter((level) => level !== "none").map((level) => <option key={level} value={level}>{formatOperationalLabel(level)}</option>)}</select></label>
          <label>Achieved minimum age<input max={120} min={1} onChange={(event) => setMinimumAge(event.target.value)} required type="number" value={minimumAge} /></label>
          <label>Manual outcome expires<input onChange={(event) => setExpiresAt(event.target.value)} required type="datetime-local" value={expiresAt} /></label>
        </> : null}
      </div>
      {verifying ? <FormMessage error message="Manual verification is exceptional and bounded. Confirm the selected assurance meets the recorded policy requirement." /> : null}
      <label>Review reason<textarea minLength={8} onChange={(event) => setReason(event.target.value)} required value={reason} /></label>
      <label className={styles.confirmation}><input checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} required type="checkbox" />I confirm this bounded outcome, reason, and audit impact. I am not treating age assurance as identity/KYC or entitlement.</label>
      <div className={styles.formActions}><button disabled={!confirmed || reason.trim().length < 8 || !verificationComplete} type="submit">Record reviewed outcome</button></div>
    </form>
  );
}

type ProbeDiagnostic = {
  id: string;
  provider: string;
  environment: string;
  status: string;
  configuration_complete: boolean;
  callback_url: string | null;
  allowed_redirect: boolean | null;
  error_code: string | null;
  capabilities: Record<string, unknown>;
};

export function ComplianceProviders() {
  const [inventory, setInventory] = useState<ProviderInventory[] | null>(null);
  const [probes, setProbes] = useState<ProviderProbe[]>([]);
  const [diagnostic, setDiagnostic] = useState<ProbeDiagnostic | null>(null);
  const [probing, setProbing] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const [nextInventory, nextProbes] = await Promise.all([
        api<ProviderInventory[]>("/admin/compliance/providers"),
        api<ProviderProbe[]>("/admin/compliance/providers/probes"),
      ]);
      setInventory(nextInventory);
      setProbes(nextProbes);
    } catch (caught) {
      setError(operationalError(caught, "Unable to load provider diagnostics."));
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function probe(provider: string) {
    setProbing(provider);
    setError("");
    try {
      const result = await api<ProbeDiagnostic>("/admin/compliance/providers/probe", {
        method: "POST",
        body: JSON.stringify({ provider }),
      });
      setDiagnostic(result);
      setMessage(`${provider} probe completed: ${result.status}. No credential value was returned.`);
      await load();
    } catch (caught) {
      setError(operationalError(caught, "Unable to run the provider probe."));
    } finally {
      setProbing("");
    }
  }

  return (
    <section className={styles.panel}>
      <SectionHeader
        action={<button className={styles.secondaryButton} onClick={() => void load()} type="button">Refresh diagnostics</button>}
        description="Read-only adapter inventory and safe reachability probes. Provider credentials remain in environment or deployment secret configuration."
        eyebrow="Provider operations"
        title="Age-assurance providers"
      />
      <FormMessage message={message} />
      <FormMessage error message={error} />
      {!inventory && !error ? <LoadingState /> : null}
      {inventory ? (
        <div className={styles.providerGrid}>
          {inventory.map((provider) => {
            const health = provider.latest_probe?.status ?? (provider.configuration_complete ? "not_probed" : "misconfigured");
            return (
              <article className={`${styles.providerCard} ${provider.selected ? styles.providerCardSelected : ""}`} key={provider.provider}>
                <div className={styles.providerHeader}>
                  <div><strong>{provider.provider}</strong><p>{provider.environment ?? "Unknown environment"}</p></div>
                  <StatusBadge value={health} />
                </div>
                <div className={styles.reviewMeta}>
                  <span>Selected adapter<strong>{provider.selected ? "Yes" : "No"}</strong></span>
                  <span>Usable now<strong>{provider.enabled ? "Yes" : "No"}</strong></span>
                  <span>Credentials configured<strong>{provider.configuration_complete ? "Yes" : "No"}</strong></span>
                  <span>Last healthy<strong>{formatDateTime(provider.last_healthy_at)}</strong></span>
                </div>
                <div className={styles.capabilities}>
                  {provider.capabilities ? Object.entries(provider.capabilities).map(([key, value]) => <span className={styles.capability} key={key}>{formatOperationalLabel(key)}: {String(value)}</span>) : <span className={styles.capability}>Capabilities unavailable</span>}
                </div>
                <div className={styles.diagnosticNote}><span aria-hidden="true">●</span>Secrets are never displayed or editable here.</div>
                <button className={styles.secondaryButton} disabled={Boolean(probing)} onClick={() => void probe(provider.provider)} type="button">{probing === provider.provider ? "Probing…" : "Run safe probe"}</button>
              </article>
            );
          })}
        </div>
      ) : null}
      {diagnostic ? (
        <div className={styles.simulationResult}>
          <div className={styles.rowBetween}><strong>Latest interactive diagnostic · {diagnostic.provider}</strong><StatusBadge value={diagnostic.status} /></div>
          <div className={styles.decisionGrid}>
            <div><span>Environment</span><strong>{diagnostic.environment}</strong></div>
            <div><span>Configuration</span><strong>{diagnostic.configuration_complete ? "Complete" : "Incomplete"}</strong></div>
            <div><span>Callback URL</span><strong>{diagnostic.callback_url ?? "Not configured"}</strong></div>
            <div><span>Allowed redirect</span><strong>{diagnostic.allowed_redirect === null ? "Not reported" : diagnostic.allowed_redirect ? "Allowed" : "Not allowed"}</strong></div>
            <div><span>Safe error code</span><strong>{diagnostic.error_code ?? "None"}</strong></div>
          </div>
        </div>
      ) : null}
      <h3>Last 100 probe records</h3>
      {probes.length ? (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead><tr><th>Provider</th><th>Environment</th><th>Status</th><th>Configuration</th><th>Callback</th><th>Probed</th><th>Error code</th></tr></thead>
            <tbody>
              {probes.map((probeRow) => (
                <tr key={probeRow.id}>
                  <td className={styles.primaryCell}>{probeRow.provider}</td>
                  <td>{probeRow.environment}</td>
                  <td><StatusBadge value={probeRow.status} /></td>
                  <td>{probeRow.configuration_complete ? "Complete" : "Incomplete"}</td>
                  <td>{probeRow.callback_url ?? "Not configured"}</td>
                  <td>{formatDateTime(probeRow.probed_at)}</td>
                  <td>{probeRow.error_code ?? "None"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <EmptyState>No provider probes recorded.</EmptyState>}
      <p className={styles.footerNote}>A degraded, unavailable, or misconfigured provider never permits restricted access. Required verification remains blocked until a valid assurance result exists.</p>
    </section>
  );
}

export function ComplianceSimulator() {
  const [country, setCountry] = useState("PT");
  const [feature, setFeature] = useState<ComplianceFeature>("adult_media");
  const [accountMode, setAccountMode] = useState<"anonymous" | "existing">("anonymous");
  const [userId, setUserId] = useState("");
  const [adultRestricted, setAdultRestricted] = useState(true);
  const [result, setResult] = useState<SimulationDecision | null>(null);
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);

  async function simulate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (accountMode === "existing" && !isUuid(userId)) {
      setError("Enter a valid existing user UUID. The simulator never invents an account state.");
      return;
    }
    setRunning(true);
    setError("");
    try {
      setResult(await api<SimulationDecision>("/admin/compliance/simulator", {
        method: "POST",
        body: JSON.stringify({
          country_code: country.trim().toUpperCase(),
          feature,
          user_id: accountMode === "existing" ? userId.trim() : null,
          adult_restricted: adultRestricted,
        }),
      }));
    } catch (caught) {
      setResult(null);
      setError(operationalError(caught, "Unable to run the compliance simulation."));
    } finally {
      setRunning(false);
    }
  }

  return (
    <section className={styles.panel}>
      <SectionHeader
        description="Calls the production policy resolver. Existing-account verification and role state come from durable server records; this screen does not implement a second rules engine."
        eyebrow="Explainability"
        title="Compliance policy simulator"
      />
      <FormMessage error message={error} />
      <form onSubmit={simulate}>
        <div className={styles.formGrid}>
          <label>Jurisdiction country<input maxLength={2} minLength={2} onChange={(event) => setCountry(event.target.value)} required value={country} /></label>
          <label>Feature<select onChange={(event) => setFeature(event.target.value as ComplianceFeature)} value={feature}>{complianceFeatures.map((value) => <option key={value} value={value}>{formatOperationalLabel(value)}</option>)}</select></label>
          <label>User state<select onChange={(event) => setAccountMode(event.target.value as typeof accountMode)} value={accountMode}><option value="anonymous">Anonymous</option><option value="existing">Existing account (server state)</option></select></label>
          {accountMode === "existing" ? <label>Existing user UUID<input onChange={(event) => setUserId(event.target.value)} required value={userId} /></label> : null}
          <label className={styles.confirmation}><input checked={adultRestricted} onChange={(event) => setAdultRestricted(event.target.checked)} type="checkbox" />Evaluate an adult-restricted context</label>
        </div>
        <p className={styles.hint}>For an existing user, current verification and assurance are loaded by the real resolver. Anonymous simulation has no durable verification. Commercial entitlement is a separate decision and is not evaluated by this endpoint.</p>
        <div className={styles.formActions}><button disabled={running} type="submit">{running ? "Evaluating…" : "Run real resolver"}</button></div>
      </form>
      {result ? (
        <article className={`${styles.simulationResult} ${result.allowed ? styles.simulationAllowed : styles.simulationDenied}`}>
          <div className={styles.rowBetween}>
            <div><p className="eyebrow">Age / feature access</p><h3>{result.allowed ? "Allowed" : "Denied"}</h3></div>
            <StatusBadge value={result.allowed ? "allowed" : "denied"} />
          </div>
          <p>{result.reason}</p>
          <div className={styles.decisionGrid}>
            <div><span>Decision code</span><strong>{result.code}</strong></div>
            <div><span>Action</span><strong>{result.action ?? "None"}</strong></div>
            <div><span>Jurisdiction</span><strong>{result.jurisdiction ?? "Unresolved"}</strong></div>
            <div><span>Policy</span><strong>{result.policy_version ? `v${result.policy_version}` : "None"}</strong></div>
            <div><span>Required age</span><strong>{result.required_minimum_age ?? "None"}</strong></div>
            <div><span>Required assurance</span><strong>{formatOperationalLabel(result.required_assurance_level)}</strong></div>
            <div><span>Achieved assurance</span><strong>{formatOperationalLabel(result.achieved_assurance_level)}</strong></div>
            <div><span>Verification expiry</span><strong>{formatDateTime(result.verification_expires_at)}</strong></div>
            <div><span>Age decision</span><strong>{result.age_access_allowed ? "Allowed" : "Denied"}</strong></div>
            <div><span>Feature decision</span><strong>{result.feature_allowed ? "Allowed" : "Denied"}</strong></div>
            <div><span>Entitlement</span><strong>Not evaluated</strong></div>
          </div>
        </article>
      ) : null}
    </section>
  );
}

export function ComplianceAudit() {
  const [data, setData] = useState<PageResult<ComplianceAuditEvent> | null>(null);
  const [page, setPage] = useState(1);
  const [searchDraft, setSearchDraft] = useState("");
  const [search, setSearch] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
    if (search) params.set("search", search);
    try {
      setData(await api<PageResult<ComplianceAuditEvent>>(`/admin/compliance/audit?${params}`));
    } catch (caught) {
      setError(operationalError(caught, "Unable to load compliance audit history."));
    }
  }, [page, search]);
  useEffect(() => { void load(); }, [load]);

  return (
    <section className={styles.panel}>
      <SectionHeader
        description="Append-oriented history across compliance, legal publication, site settings, performer, consent, and creator verification. Sensitive-looking metadata keys are redacted again in the browser."
        eyebrow="Evidence"
        title="Compliance audit trail"
      />
      <FormMessage error message={error} />
      <form className={styles.filters} onSubmit={(event) => { event.preventDefault(); setPage(1); setSearch(searchDraft.trim()); }}>
        <label>Search event type<input onChange={(event) => setSearchDraft(event.target.value)} placeholder="compliance.policy" value={searchDraft} /></label>
        <button className={styles.secondaryButton} type="submit">Search audit</button>
        {search ? <button className={styles.ghostButton} onClick={() => { setSearch(""); setSearchDraft(""); setPage(1); }} type="button">Clear</button> : <span />}
      </form>
      {!data && !error ? <LoadingState /> : null}
      {data ? (
        <>
          {data.items.length ? (
            <ul className={styles.auditList}>
              {data.items.map((event) => (
                <li className={styles.auditItem} key={event.id}>
                  <div className={styles.rowBetween}>
                    <strong>{event.event_type}</strong>
                    <time dateTime={event.created_at}>{formatDateTime(event.created_at)}</time>
                  </div>
                  <p>Actor: {event.actor_user_id ?? "system"} · Target: {event.target_type ?? "none"}{event.target_id ? ` / ${event.target_id}` : ""}</p>
                  <p>Request: {event.correlation_id ?? "not recorded"} · IP: {event.ip_address ?? "not recorded"} · Agent: {event.user_agent ?? "not recorded"}</p>
                  <pre className={styles.auditMetadata}>{JSON.stringify(safeAuditMetadata(event.metadata), null, 2)}</pre>
                </li>
              ))}
            </ul>
          ) : <EmptyState>No audit events match this event-type search.</EmptyState>}
          <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />
        </>
      ) : null}
    </section>
  );
}

export function ComplianceRelatedControls() {
  const controls = [
    {
      title: "Viewer age assurance",
      status: "Managed in this control room",
      body: "Provider-backed viewer access verification, expiry, revocation, review, jurisdiction, and assurance level.",
      href: "/admin/compliance#verifications",
      link: "Open age verification",
    },
    {
      title: "Creator identity / KYC",
      status: "Separate creator authority",
      body: "Creator identity, adult status, verification expiry, and payout eligibility are not inferred from viewer age assurance.",
      href: "/moderation",
      link: "Open Trust & Safety operations",
    },
    {
      title: "Performer verification",
      status: "Private performer records",
      body: "Linked performer identity and age records remain private and are evaluated per content association. No general performer-list API is exposed here.",
      href: "/moderation",
      link: "Open moderation queue",
    },
    {
      title: "Consent / releases",
      status: "Separate evidence workflow",
      body: "Release validity is linked to relevant content and performer records; age or identity verification never substitutes for consent.",
      href: "/moderation/consent",
      link: "Open consent review",
    },
    {
      title: "Legal documents",
      status: "Versioned legal CMS",
      body: "Legal publication and exact user acceptance are managed separately from policy resolver configuration.",
      href: "/admin/legal",
      link: "Open Legal & Policies",
    },
  ];

  return (
    <section className={styles.panel}>
      <SectionHeader
        description="A map to adjacent authorities. These links do not imply that one status satisfies another."
        eyebrow="Separation of concerns"
        title="KYC, performers, consent, and legal"
      />
      <div className={styles.relatedGrid}>
        {controls.map((control) => (
          <article className={styles.relatedCard} key={control.title}>
            <StatusBadge value="separate" label={control.status} />
            <h3>{control.title}</h3>
            <p>{control.body}</p>
            <Link className="link" href={control.href}>{control.link} →</Link>
          </article>
        ))}
      </div>
    </section>
  );
}
