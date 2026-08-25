"use client";

import { useEffect, useState } from "react";

import { api, ApiError } from "../lib/api";

type Currency = Record<string, string | number | null> & { currency: string };
type Report = { metric_definition_version: string; currencies: Currency[] };

function MetricList({ rows }: { rows: Currency[] }) {
  return <ul>{rows.map((row) => <li key={row.currency}><strong>{row.currency}</strong> {Object.entries(row).filter(([key]) => key !== "currency").map(([key, value]) => <span key={key}> · {key.replaceAll("_", " ")}: {value ?? "—"}</span>)}</li>)}</ul>;
}

export function AnalyticsDashboard({ scope }: { scope: "creator" | "platform" }) {
  const [reports, setReports] = useState<Record<string, Report>>({});
  const [error, setError] = useState("");
  const [startsAt, setStartsAt] = useState("");
  const [endsAt, setEndsAt] = useState("");
  const query = new URLSearchParams();
  if (startsAt) query.set("starts_at", new Date(startsAt).toISOString());
  if (endsAt) query.set("ends_at", new Date(endsAt).toISOString());
  const suffix = query.size ? `?${query}` : "";
  useEffect(() => {
    const paths = scope === "creator" ? ["overview", "messaging", "private-live", "marketplace", "referrals", "featuring"] : ["overview"];
    void Promise.all(paths.map(async (path) => [path, await api<Report>(`/analytics/${scope}/${path}${suffix}`)] as const)).then((items) => setReports(Object.fromEntries(items))).catch((reason: unknown) => setError(reason instanceof ApiError ? reason.message : "Unable to load analytics"));
  }, [scope, suffix]);
  return <section><p className="eyebrow">ANALYTICS</p><h2>{scope === "creator" ? "Performance" : "Platform BI"}</h2><label>Start (UTC)<input type="datetime-local" value={startsAt} onChange={(event) => setStartsAt(event.target.value)} /></label><label>End (UTC)<input type="datetime-local" value={endsAt} onChange={(event) => setEndsAt(event.target.value)} /></label>{Object.entries(reports).map(([name, report]) => <article key={name}><h3>{name.replaceAll("-", " ")}</h3><MetricList rows={report.currencies} /><small>Definition: {report.metric_definition_version}</small></article>)}{error && <p className="error" role="alert">{error}</p>}</section>;
}

export function GroupAnalyticsDashboard() {
  const [groupId, setGroupId] = useState("");
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState("");
  async function load() {
    try { setReport(await api<Report>(`/analytics/groups/${groupId}/overview`)); setError(""); }
    catch (reason) { setError(reason instanceof ApiError ? reason.message : "Unable to load group analytics"); }
  }
  return <section><label>Group ID<input value={groupId} onChange={(event) => setGroupId(event.target.value)} /></label><button type="button" onClick={() => void load()} disabled={!groupId}>Load analytics</button>{report && <MetricList rows={report.currencies} />}{error && <p className="error" role="alert">{error}</p>}</section>;
}
