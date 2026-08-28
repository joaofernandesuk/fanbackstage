"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { api, ApiError } from "../lib/api";
import type { SiteSettings, SiteSocialLink } from "../lib/legal";
import styles from "./legal-admin.module.css";

function dateInput(value: string | null) {
  if (!value) return "";
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

function optionalIso(value: FormDataEntryValue | null) {
  const raw = String(value ?? "").trim();
  return raw ? new Date(raw).toISOString() : null;
}

export function parseSocialLinks(value: string): SiteSocialLink[] {
  return value.split("\n").map((line) => line.trim()).filter(Boolean).map((line) => {
    const divider = line.indexOf("|");
    if (divider < 1) throw new Error("Each social link must use Label | https://example format.");
    const label = line.slice(0, divider).trim();
    const url = line.slice(divider + 1).trim();
    if (url.includes("\\") || /[\u0000-\u001f]/.test(url)) {
      throw new Error("Social links contain unsupported characters.");
    }
    const parsed = new URL(url);
    if (parsed.protocol !== "https:" || parsed.username || parsed.password) {
      throw new Error("Social links must use HTTPS URLs without credentials.");
    }
    return { label, url: parsed.toString() };
  });
}

export function SiteSettingsEditor() {
  const [settings, setSettings] = useState<SiteSettings | null>(null);
  const [message, setMessage] = useState("");
  const load = useCallback(async () => {
    try {
      setSettings(await api<SiteSettings>("/admin/site-settings"));
    } catch (caught) {
      setMessage(caught instanceof ApiError && caught.status < 500
        ? caught.message
        : "Unable to load site settings.");
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const optional = (name: string) => String(form.get(name) ?? "").trim() || null;
    try {
      const updated = await api<SiteSettings>("/admin/site-settings", {
        method: "PUT",
        body: JSON.stringify({
          support_email: optional("support_email"),
          footer_text: optional("footer_text"),
          public_contact_text: optional("public_contact_text"),
          social_links: parseSocialLinks(String(form.get("social_links") ?? "")),
          homepage_announcement: optional("homepage_announcement"),
          maintenance_notice: optional("maintenance_notice"),
          banner_level: form.get("banner_level"),
          banner_starts_at: optionalIso(form.get("banner_starts_at")),
          banner_ends_at: optionalIso(form.get("banner_ends_at")),
          reason: String(form.get("reason") ?? "").trim(),
        }),
      });
      setSettings(updated);
      setMessage(`Published site settings version ${updated.version}.`);
    } catch (caught) {
      setMessage(caught instanceof Error && (!(caught instanceof ApiError) || caught.status < 500)
        ? caught.message
        : "Unable to update site settings.");
    }
  }

  if (!settings) return <section className="card"><p>{message || "Loading site settings…"}</p></section>;
  return (
    <div className={styles.shell}>
      <section className="card">
        <p className="eyebrow">Public shell</p>
        <h1>Site settings</h1>
        <p>Every save creates an audited immutable version. Empty optional fields are published as cleared.</p>
        <p className={styles.muted}>Current version: {settings.version || "No saved version"}</p>
        {message && <p role="status">{message}</p>}
      </section>
      <section className="card">
        <form key={settings.version} onSubmit={save}>
          <div className={styles.grid}>
            <label>Support email<input defaultValue={settings.support_email ?? ""} name="support_email" type="email" /></label>
            <label>Banner level<select defaultValue={settings.banner_level} name="banner_level"><option value="info">Info</option><option value="warning">Warning</option><option value="critical">Critical</option></select></label>
          </div>
          <label>Footer text<textarea defaultValue={settings.footer_text ?? ""} maxLength={500} name="footer_text" /></label>
          <label>Public contact text<textarea defaultValue={settings.public_contact_text ?? ""} maxLength={1000} name="public_contact_text" /></label>
          <label>Social links (one per line)<textarea defaultValue={settings.social_links.map((link) => `${link.label} | ${link.url}`).join("\n")} name="social_links" placeholder="Instagram | https://instagram.com/fanbackstage" /></label>
          <label>Homepage announcement<textarea defaultValue={settings.homepage_announcement ?? ""} maxLength={2000} name="homepage_announcement" /></label>
          <label>Maintenance notice<textarea defaultValue={settings.maintenance_notice ?? ""} maxLength={2000} name="maintenance_notice" /></label>
          <div className={styles.grid}>
            <label>Banner starts<input defaultValue={dateInput(settings.banner_starts_at)} name="banner_starts_at" type="datetime-local" /></label>
            <label>Banner ends<input defaultValue={dateInput(settings.banner_ends_at)} name="banner_ends_at" type="datetime-local" /></label>
          </div>
          <label>Change reason<textarea minLength={8} name="reason" required /></label>
          <button type="submit">Publish new settings version</button>
        </form>
      </section>
    </div>
  );
}
