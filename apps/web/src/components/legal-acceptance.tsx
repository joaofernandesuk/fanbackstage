"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "../lib/api";
import type { LegalAcceptance, LegalDocument } from "../lib/legal";
import {
  initialLegalAcceptanceSelection,
  legalDocumentPath,
  legalGateBypasses,
  reconcileLegalAcceptanceSelection,
} from "../lib/legal";
import styles from "./legal.module.css";

export function LegalAcceptanceGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const bypass = legalGateBypasses(pathname);
  const [documents, setDocuments] = useState<LegalDocument[] | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const result = await api<{ documents: LegalDocument[] }>("/legal/me/requirements");
      setDocuments(result.documents);
      const versionIds = result.documents.map((item) => item.version_id);
      setSelected((current) => current.size
        ? reconcileLegalAcceptanceSelection(current, versionIds)
        : initialLegalAcceptanceSelection(versionIds));
      setError("");
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        setDocuments([]);
        return;
      }
      setDocuments(null);
      setError(caught instanceof ApiError ? caught.message : "Unable to confirm legal requirements.");
    }
  }, []);

  useEffect(() => {
    if (!bypass) {
      setDocuments(null);
      void load();
    }
  }, [bypass, load, pathname]);

  async function accept() {
    if (!documents || selected.size !== documents.length) return;
    setSaving(true);
    setError("");
    try {
      await api("/legal/acceptances", {
        method: "POST",
        body: JSON.stringify({ version_ids: [...selected], source: "interstitial" }),
      });
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unable to save your acceptance.");
    } finally {
      setSaving(false);
    }
  }

  if (bypass || documents?.length === 0) return <>{children}</>;
  return (
    <div aria-modal="true" className={styles.gate} role="dialog">
      <section className={`${styles.gateCard} card`}>
        <p className="eyebrow">Updated terms</p>
        <h1>Review before continuing</h1>
        {error && <p className="error" role="alert">{error}</p>}
        {error && documents === null ? (
          <button onClick={() => void load()} type="button">Try again</button>
        ) : null}
        {documents?.map((document) => (
          <label className={styles.checkRow} key={document.version_id}>
            <input
              checked={selected.has(document.version_id)}
              onChange={(event) => setSelected((current) => {
                const next = new Set(current);
                if (event.target.checked) next.add(document.version_id);
                else next.delete(document.version_id);
                return next;
              })}
              type="checkbox"
            />
            <span>
              I accept <Link className={styles.legalLink} href={legalDocumentPath(document.slug)} target="_blank">{document.title} (version {document.version})</Link>.
            </span>
          </label>
        ))}
        {documents ? (
          <button disabled={saving || selected.size !== documents.length} onClick={() => void accept()} type="button">
            {saving ? "Saving…" : "Accept and continue"}
          </button>
        ) : !error ? <p>Checking current legal requirements…</p> : null}
      </section>
    </div>
  );
}

export function LegalAcceptanceHistory() {
  const [items, setItems] = useState<LegalAcceptance[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api<LegalAcceptance[]>("/legal/me/acceptances")
      .then(setItems)
      .catch((caught: unknown) => setError(
        caught instanceof ApiError ? caught.message : "Unable to load legal acceptance history.",
      ));
  }, []);
  return (
    <section className="card">
      <p className="eyebrow">Legal</p>
      <h2>Acceptance history</h2>
      {error && <p className="error" role="alert">{error}</p>}
      {!items && !error ? <p>Loading acceptance history…</p> : null}
      {items?.length === 0 ? <p>No legal document acceptances are recorded yet.</p> : null}
      {items?.length ? (
        <ul className={styles.history}>
          {items.map((item) => (
            <li key={item.acceptance_id}>
              <strong>{item.title}</strong>
              <div className={styles.muted}>
                Version {item.version} · {new Date(item.accepted_at).toLocaleString()}
                {item.jurisdiction_code ? ` · ${item.jurisdiction_code}` : ""}
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
