"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ApiError, api } from "../lib/api";
import styles from "./admin-operations-dashboard.module.css";

type Queue = {
  key: string;
  label: string;
  count: number;
  href: string;
  description: string;
};

export function AdminOperationsDashboard() {
  const [queues, setQueues] = useState<Queue[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void api<{ queues: Queue[] }>("/admin/operations/overview")
      .then((snapshot) => {
        if (active) setQueues(snapshot.queues);
      })
      .catch((caught: unknown) => {
        if (active) setError(caught instanceof ApiError ? caught.message : "Unable to load operations overview.");
      });
    return () => { active = false; };
  }, []);

  return (
    <main className={styles.page}>
      <header className={styles.hero}>
        <p className="eyebrow">Admin operations</p>
        <h1>What needs attention now</h1>
        <p>Start with an owned queue. Decisions remain server-authorised, auditable, and separated by role.</p>
      </header>
      {error ? <p className={styles.error} role="alert">{error}</p> : null}
      {!error && !queues.length ? <p className={styles.loading}>Loading operational queues…</p> : null}
      <section aria-label="Operational queues" className={styles.grid}>
        {queues.map((queue) => (
          <Link className={styles.queue} href={queue.href} key={queue.key}>
            <span className={styles.count}>{queue.count}</span>
            <div>
              <h2>{queue.label}</h2>
              <p>{queue.description}</p>
              <span className={styles.open}>Open queue <span aria-hidden="true">→</span></span>
            </div>
          </Link>
        ))}
      </section>
      <section className={styles.guidance}>
        <div>
          <strong>Creator applications</strong>
          <p>Only applications with a completed identity check can be approved or rejected. Applications still in verification are visible, but deliberately have no decision controls.</p>
        </div>
        <div>
          <strong>Fast, not unsafe</strong>
          <p>Use the dedicated queue for each domain. The dashboard does not combine moderation, KYC evidence, legal decisions, or financial controls into one unsafe bulk action.</p>
        </div>
      </section>
    </main>
  );
}
