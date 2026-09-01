"use client";

import { useEffect, useState } from "react";

import { ApiError, api } from "../lib/api";
import styles from "./creator-applications.module.css";

type Verification = {
  status: string;
  provider: string | null;
  identity_verified: boolean;
  adult_verified: boolean;
  expires_at: string | null;
  failure_reason_code: string | null;
};

type Application = {
  id: string;
  email: string;
  username: string | null;
  display_name: string | null;
  status: string;
  submitted_at: string;
  country_code: string | null;
  verification: Verification;
  review_ready: boolean;
  rejection_reason: string | null;
  profile: { bio: string | null; categories: string[]; languages: string[]; location: string | null };
};

const FILTERS = ["pending_review", "pending_verification", "rejected", "approved"] as const;
type ApplicationStatus = (typeof FILTERS)[number];

function isApplicationStatus(value: string | undefined): value is ApplicationStatus {
  return Boolean(value && FILTERS.includes(value as ApplicationStatus));
}

export function CreatorApplications({ initialStatus }: { initialStatus?: string }) {
  const [status, setStatus] = useState<ApplicationStatus>(
    isApplicationStatus(initialStatus) ? initialStatus : "pending_review",
  );
  const [applications, setApplications] = useState<Application[]>([]);
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(true);

  async function load(nextStatus = status) {
    setLoading(true);
    setMessage("");
    try {
      setApplications(await api<Application[]>(`/admin/creator-applications?status=${nextStatus}`));
    } catch (caught) {
      setMessage(caught instanceof ApiError ? caught.message : "Unable to load creator applications.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, [status]);

  async function decide(application: Application, action: "approve" | "reject") {
    const reason = reasons[application.id]?.trim() ?? "";
    if (reason.length < 3) {
      setMessage("Add a short decision reason before recording this creator review.");
      return;
    }
    try {
      await api(`/admin/creator-applications/${application.id}/${action}`, {
        method: "POST",
        body: JSON.stringify({ reason }),
      });
      setMessage(`Creator application ${action === "approve" ? "approved" : "rejected"}.`);
      await load();
    } catch (caught) {
      setMessage(caught instanceof ApiError ? caught.message : "Unable to record creator decision.");
    }
  }

  return (
    <main className={styles.page}>
      <header className={styles.hero}>
        <p className="eyebrow">Creator operations</p>
        <h1>Creator applications</h1>
        <p>Review a complete application in context. Approval is available only after the current identity check has verified.</p>
      </header>
      <div className={styles.filters} role="tablist" aria-label="Creator application status">
        {FILTERS.map((item) => <button aria-selected={status === item} className={status === item ? styles.activeFilter : undefined} key={item} onClick={() => setStatus(item)} role="tab" type="button">{item.replaceAll("_", " ")}</button>)}
      </div>
      {message ? <p className={styles.message} role="status">{message}</p> : null}
      {loading ? <p className={styles.loading}>Loading applications…</p> : null}
      {!loading && !applications.length ? <section className={styles.empty}><h2>No {status.replaceAll("_", " ")} applications</h2><p>There is nothing in this queue right now.</p></section> : null}
      <section className={styles.list}>
        {applications.map((application) => (
          <article className={styles.application} key={application.id}>
            <div className={styles.person}>
              <div><h2>{application.display_name ?? application.username ?? "Unnamed creator"}</h2><p>@{application.username ?? "not set"} · {application.email}</p></div>
              <span className={`${styles.badge} ${application.review_ready ? styles.ready : ""}`}>{application.status.replaceAll("_", " ")}</span>
            </div>
            <dl className={styles.metadata}>
              <div><dt>Application</dt><dd>{new Date(application.submitted_at).toLocaleString()}</dd></div>
              <div><dt>Account country</dt><dd>{application.country_code ?? "Unresolved"}</dd></div>
              <div><dt>KYC provider</dt><dd>{application.verification.provider ?? "Not started"}</dd></div>
              <div><dt>KYC status</dt><dd>{application.verification.status.replaceAll("_", " ")}</dd></div>
            </dl>
            <div className={styles.profileContext}>
              <p><strong>Profile summary</strong>{application.profile.bio ? ` · ${application.profile.bio}` : " · No profile bio provided."}</p>
              <p><strong>Discovery</strong>{application.profile.categories.length ? ` · ${application.profile.categories.join(", ")}` : " · No categories selected."}{application.profile.languages.length ? ` · ${application.profile.languages.join(", ")}` : ""}</p>
              {application.profile.location ? <p><strong>Configured location</strong> · {application.profile.location}</p> : null}
            </div>
            {application.review_ready ? (
              <div className={styles.review}>
                <label>Decision reason<textarea onChange={(event) => setReasons((current) => ({ ...current, [application.id]: event.target.value }))} placeholder="Record the review basis for the audit trail." value={reasons[application.id] ?? ""} /></label>
                <div><button className={styles.approve} onClick={() => void decide(application, "approve")} type="button">Approve creator</button><button className={styles.reject} onClick={() => void decide(application, "reject")} type="button">Reject application</button></div>
              </div>
            ) : <p className={styles.waiting}>This application is not yet actionable. It will notify authorised admins when identity verification reaches the review queue.</p>}
          </article>
        ))}
      </section>
    </main>
  );
}
