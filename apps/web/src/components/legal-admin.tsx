"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";

import { api, ApiError } from "../lib/api";
import {
  displayLegalType,
  LEGAL_DOCUMENT_TYPES,
  legalBlocksToMarkdown,
  legalMarkdownToBlocks,
  type LegalAudience,
  type LegalDocument,
  type LegalDocumentDetail,
  type LegalDocumentPage,
} from "../lib/legal";
import styles from "./legal-admin.module.css";
import { LegalBody } from "./legal-document";

const audiences: LegalAudience[] = ["all_users", "fan", "creator", "group_manager", "affiliate"];

function errorMessage(caught: unknown, fallback: string) {
  return caught instanceof ApiError && caught.status < 500 ? caught.message : fallback;
}

function optionalIso(value: FormDataEntryValue | null) {
  const raw = String(value ?? "").trim();
  return raw ? new Date(raw).toISOString() : null;
}

function dateInput(value: string | null) {
  if (!value) return "";
  const date = new Date(value);
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function draftPayload(form: FormData) {
  return {
    title: String(form.get("title") ?? "").trim(),
    body: legalMarkdownToBlocks(String(form.get("body") ?? "")),
    effective_from: optionalIso(form.get("effective_from")),
    effective_until: optionalIso(form.get("effective_until")),
    requires_acceptance: form.get("requires_acceptance") === "on",
    requires_legal_review: form.get("legal_review_complete") !== "on",
    approved_for_publication: form.get("approved_for_publication") === "on",
    is_demo: form.get("is_demo") === "on",
  };
}

export function LegalAdminDashboard() {
  const [page, setPage] = useState<LegalDocumentPage | null>(null);
  const [message, setMessage] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const load = useCallback(async () => {
    try {
      setPage(await api<LegalDocumentPage>("/admin/legal/documents?limit=100"));
    } catch (caught) {
      setMessage(errorMessage(caught, "Unable to load legal documents."));
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage("");
    const form = new FormData(event.currentTarget);
    try {
      const jurisdiction = String(form.get("jurisdiction_code") ?? "").trim();
      const created = await api<LegalDocument>("/admin/legal/documents", {
        method: "POST",
        body: JSON.stringify({
          document_type: form.get("document_type"),
          slug: String(form.get("slug") ?? "").trim(),
          jurisdiction_code: jurisdiction || null,
          language: String(form.get("language") ?? "en").trim(),
          audience: form.get("audience"),
          ...draftPayload(form),
        }),
      });
      setMessage(`Created ${created.title} version ${created.version} as a draft.`);
      setShowCreate(false);
      await load();
    } catch (caught) {
      setMessage(errorMessage(caught, "Unable to create the legal document."));
    }
  }

  return (
    <div className={styles.shell}>
      <section className="card">
        <div className={styles.toolbar}>
          <div><p className="eyebrow">Compliance content</p><h1>Legal documents</h1></div>
          <button onClick={() => setShowCreate((value) => !value)} type="button">
            {showCreate ? "Cancel" : "New document"}
          </button>
        </div>
        <p>Drafts are editable. Published text is immutable; changes require a new version.</p>
        {message && <p role="status">{message}</p>}
      </section>

      {showCreate ? (
        <section className="card">
          <h2>Create legal draft</h2>
          <LegalDraftForm onSubmit={create} />
        </section>
      ) : null}

      <section className="card">
        <h2>Document versions</h2>
        {!page && !message ? <p>Loading legal documents…</p> : null}
        <div className={styles.grid}>
          {page?.items.map((item) => (
            <article className={styles.row} key={item.version_id}>
              <div>
                <strong>{item.title}</strong>
                <p className={styles.muted}>
                  {displayLegalType(item.document_type)} · v{item.version} · {item.jurisdiction_code ?? "Global"} · {item.audience}
                </p>
              </div>
              <div>
                <span className={styles.badge}>{item.status}</span>{" "}
                <Link className="link" href={`/admin/legal/${item.document_id}`}>Open</Link>
              </div>
            </article>
          ))}
        </div>
        {page?.items.length === 0 ? <p>No legal drafts or versions exist.</p> : null}
      </section>
    </div>
  );
}

function LegalDraftForm({
  initial,
  onSubmit,
  submitLabel = "Create draft",
}: {
  initial?: LegalDocument;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void | Promise<void>;
  submitLabel?: string;
}) {
  const [markdown, setMarkdown] = useState(
    initial ? legalBlocksToMarkdown(initial.body) : "",
  );
  let preview = null;
  let previewError = "";
  try {
    preview = markdown.trim() ? legalMarkdownToBlocks(markdown) : null;
  } catch (caught) {
    previewError = caught instanceof Error ? caught.message : "Preview is unavailable.";
  }
  return (
    <form onSubmit={onSubmit}>
      {!initial ? (
        <div className={styles.grid}>
          <label>Document type<select defaultValue="terms" name="document_type">{LEGAL_DOCUMENT_TYPES.map((type) => <option key={type} value={type}>{displayLegalType(type)}</option>)}</select></label>
          <label>Slug<input name="slug" pattern="[a-z0-9]+(?:-[a-z0-9]+)*" placeholder="terms" required /></label>
          <label>Jurisdiction (optional ISO code)<input maxLength={2} name="jurisdiction_code" placeholder="Global" /></label>
          <label>Language<input defaultValue="en" name="language" required /></label>
          <label>Audience<select defaultValue="all_users" name="audience">{audiences.map((audience) => <option key={audience} value={audience}>{displayLegalType(audience)}</option>)}</select></label>
        </div>
      ) : null}
      <label>Title<input defaultValue={initial?.title} maxLength={200} name="title" required /></label>
      <label>Plain Markdown
        <textarea className={styles.editor} name="body" onChange={(event) => setMarkdown(event.target.value)} placeholder="## Overview" required value={markdown} />
      </label>
      <p className={styles.muted}>Supported: H2–H4 headings, paragraphs, ordered/unordered lists, callouts, and one safe link per line. Raw HTML is rendered as plain text.</p>
      <section className={styles.preview}>
        <h3>Safe preview</h3>
        {preview ? <LegalBody blocks={preview} /> : <p>{previewError || "Add content to preview it."}</p>}
      </section>
      <div className={styles.grid}>
        <label>Effective from<input defaultValue={dateInput(initial?.effective_from ?? null)} name="effective_from" type="datetime-local" /></label>
        <label>Effective until<input defaultValue={dateInput(initial?.effective_until ?? null)} name="effective_until" type="datetime-local" /></label>
      </div>
      <div className={styles.checks}>
        <label className={styles.check}><input defaultChecked={initial?.requires_acceptance} name="requires_acceptance" type="checkbox" />Requires exact acceptance</label>
        <label className={styles.check}><input defaultChecked={initial ? !initial.requires_legal_review : false} name="legal_review_complete" type="checkbox" />Legal review complete</label>
        <label className={styles.check}><input defaultChecked={initial?.approved_for_publication} name="approved_for_publication" type="checkbox" />Approved for publication</label>
        <label className={styles.check}><input defaultChecked={initial?.is_demo} name="is_demo" type="checkbox" />Demo content</label>
      </div>
      <button type="submit">{submitLabel}</button>
    </form>
  );
}

export function LegalDocumentEditor({ documentId }: { documentId: string }) {
  const [detail, setDetail] = useState<LegalDocumentDetail | null>(null);
  const [version, setVersion] = useState<LegalDocument | null>(null);
  const [message, setMessage] = useState("");
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);

  const loadDetail = useCallback(async (preferredVersion?: string) => {
    try {
      const next = await api<LegalDocumentDetail>(`/admin/legal/documents/${encodeURIComponent(documentId)}`);
      setDetail(next);
      const versionId = preferredVersion ?? next.versions[0]?.version_id;
      if (versionId) setVersion(await api<LegalDocument>(`/admin/legal/versions/${versionId}`));
    } catch (caught) {
      setMessage(errorMessage(caught, "Unable to load the legal document."));
    }
  }, [documentId]);
  useEffect(() => { void loadDetail(); }, [loadDetail]);

  async function selectVersion(versionId: string) {
    setMessage("");
    try {
      setVersion(await api<LegalDocument>(`/admin/legal/versions/${versionId}`));
    } catch (caught) {
      setMessage(errorMessage(caught, "Unable to load this legal version."));
    }
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!version) return;
    try {
      const updated = await api<LegalDocument>(`/admin/legal/versions/${version.version_id}`, {
        method: "PATCH",
        body: JSON.stringify(draftPayload(new FormData(event.currentTarget))),
      });
      setVersion(updated);
      setMessage(`Saved draft version ${updated.version}.`);
      await loadDetail(updated.version_id);
    } catch (caught) {
      setMessage(errorMessage(caught, "Unable to save this legal draft."));
    }
  }

  async function createVersion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      const created = await api<LegalDocument>(`/admin/legal/documents/${encodeURIComponent(documentId)}/versions`, {
        method: "POST",
        body: JSON.stringify(draftPayload(new FormData(event.currentTarget))),
      });
      setMessage(`Created draft version ${created.version}.`);
      await loadDetail(created.version_id);
    } catch (caught) {
      setMessage(errorMessage(caught, "Unable to create a new legal version."));
    }
  }

  async function transition(action: "publish" | "retire") {
    if (!version || !confirmed || reason.trim().length < 8) return;
    try {
      const updated = await api<LegalDocument>(`/admin/legal/versions/${version.version_id}/${action}`, {
        method: "POST",
        body: JSON.stringify({ confirmed: true, reason: reason.trim() }),
      });
      setVersion(updated);
      setMessage(`${updated.title} version ${updated.version} is ${updated.status}.`);
      setConfirmed(false);
      setReason("");
      await loadDetail(updated.version_id);
    } catch (caught) {
      setMessage(errorMessage(caught, `Unable to ${action} this legal version.`));
    }
  }

  if (!detail || !version) return <section className="card"><p>{message || "Loading legal editor…"}</p></section>;
  return (
    <div className={styles.shell}>
      <section className="card">
        <Link className="link" href="/admin/legal">← Legal documents</Link>
        <p className="eyebrow">{displayLegalType(detail.document_type)}</p>
        <h1>{version.title}</h1>
        <p>{detail.slug} · {detail.jurisdiction_code ?? "Global"} · {detail.language} · {detail.audience}</p>
        {message && <p role="status">{message}</p>}
      </section>
      <div className={styles.grid}>
        <section className="card">
          <h2>Versions</h2>
          <ul className={styles.versionList}>{detail.versions.map((item) => (
            <li key={item.version_id}>
              <button className={`${styles.versionButton} ${item.version_id === version.version_id ? styles.selected : ""}`} onClick={() => void selectVersion(item.version_id)} type="button">
                Version {item.version} · {item.status}{item.is_demo ? " · Demo" : ""}
              </button>
            </li>
          ))}</ul>
        </section>
        <section className="card">
          <h2>{version.status === "draft" ? "Edit draft" : "Immutable version snapshot"}</h2>
          {version.status === "draft"
            ? <LegalDraftForm initial={version} key={version.version_id} onSubmit={save} submitLabel="Save draft" />
            : <LegalDraftForm initial={version} key={version.version_id} onSubmit={createVersion} submitLabel="Create next version" />}
        </section>
      </div>
      {(version.status === "draft" || version.status === "published") ? (
        <section className={`${styles.dangerZone} card`}>
          <h2>{version.status === "draft" ? "Publish version" : "Retire version"}</h2>
          <p>Publication and retirement are audited privileged actions. They do not modify the saved body.</p>
          <label>Reason<textarea onChange={(event) => setReason(event.target.value)} value={reason} /></label>
          <label className={styles.check}><input checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} type="checkbox" />I confirm this legal lifecycle change.</label>
          <div className={styles.actionRow}>
            <button disabled={!confirmed || reason.trim().length < 8} onClick={() => void transition(version.status === "draft" ? "publish" : "retire")} type="button">
              {version.status === "draft" ? "Publish reviewed version" : "Retire published version"}
            </button>
          </div>
        </section>
      ) : null}
    </div>
  );
}
