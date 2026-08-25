"use client";

import { useEffect, useMemo, useState } from "react";

import { api, ApiError } from "../lib/api";

type Currency = Record<string, string | number | null> & { currency: string };
type Report = { metric_definition_version?: string; currencies: Currency[]; revenue_sources?: Currency[] };

function isoInput(daysAgo: number) {
  return new Date(Date.now() - daysAgo * 86_400_000).toISOString().slice(0, 16);
}

function reportQuery(startsAt: string, endsAt: string, currency: string) {
  const query = new URLSearchParams();
  if (startsAt) query.set("starts_at", new Date(startsAt).toISOString());
  if (endsAt) query.set("ends_at", new Date(endsAt).toISOString());
  if (currency) query.set("currency", currency);
  return query.toString() ? `?${query}` : "";
}

function MetricList({ rows }: { rows: Currency[] }) {
  if (!rows.length) return <p>No reportable activity in this period.</p>;
  return <ul>{rows.map((row, index) => <li key={`${row.currency}-${String(row.source ?? index)}`}><strong>{row.currency}</strong>{Object.entries(row).filter(([key]) => key !== "currency").map(([key, value]) => <span key={key}> · {key.replaceAll("_", " ")}: {value ?? "—"}</span>)}</li>)}</ul>;
}

function Controls({ startsAt, endsAt, currency, source, onChange }: { startsAt: string; endsAt: string; currency: string; source: string; onChange: (name: string, value: string) => void }) {
  return <fieldset><legend>Report filters</legend><label>Start (UTC)<input type="datetime-local" value={startsAt} onChange={(event) => onChange("startsAt", event.target.value)} /></label><label>End (UTC)<input type="datetime-local" value={endsAt} onChange={(event) => onChange("endsAt", event.target.value)} /></label><label>Currency<input aria-label="Currency" maxLength={3} placeholder="All" value={currency} onChange={(event) => onChange("currency", event.target.value.toUpperCase())} /></label><label>Source<input aria-label="Source" placeholder="All sources" value={source} onChange={(event) => onChange("source", event.target.value)} /></label></fieldset>;
}

function DashboardReport({ report, source, title }: { report: Report; source: string; title: string }) {
  const sources = useMemo(() => (report.revenue_sources ?? []).filter((row) => !source || String(row.source).toLowerCase() === source.toLowerCase()), [report.revenue_sources, source]);
  return <><article><h3>{title} KPIs</h3><MetricList rows={report.currencies} /></article>{report.revenue_sources && <article><h3>Source breakdown</h3><MetricList rows={sources} /></article>}<small>Definition: {report.metric_definition_version ?? "phase14.v1"}</small></>;
}

export function AnalyticsDashboard({ scope }: { scope: "creator" | "platform" }) {
  const [report, setReport] = useState<Report | null>(null); const [previous, setPrevious] = useState<Report | null>(null); const [error, setError] = useState(""); const [loading, setLoading] = useState(true);
  const [startsAt, setStartsAt] = useState(() => isoInput(30)); const [endsAt, setEndsAt] = useState(() => isoInput(0)); const [currency, setCurrency] = useState(""); const [source, setSource] = useState(""); const query = reportQuery(startsAt, endsAt, currency);
  useEffect(() => { let live = true; setLoading(true); setError(""); const start = new Date(startsAt); const end = new Date(endsAt); const previousStart = new Date(start.getTime() - (end.getTime() - start.getTime())); void Promise.all([api<Report>(`/analytics/${scope}/overview${query}`), api<Report>(`/analytics/${scope}/overview${reportQuery(previousStart.toISOString().slice(0, 16), startsAt, currency)}`)]).then(([current, prior]) => { if (live) { setReport(current); setPrevious(prior); } }).catch((reason: unknown) => { if (live) setError(reason instanceof ApiError && reason.status === 403 ? "You do not have permission to view this analytics scope." : "Unable to load analytics."); }).finally(() => { if (live) setLoading(false); }); return () => { live = false; }; }, [scope, query, startsAt, endsAt, currency]);
  const change = (name: string, value: string) => ({ startsAt: setStartsAt, endsAt: setEndsAt, currency: setCurrency, source: setSource }[name] as (value: string) => void)(value);
  return <section><Controls {...{ startsAt, endsAt, currency, source, onChange: change }} />{loading && <p role="status">Loading analytics…</p>}{error && <p className="error" role="alert">{error}</p>}{!loading && !error && report && <><DashboardReport report={report} source={source} title="Current period" /><article><h3>Period comparison</h3>{previous ? <MetricList rows={previous.currencies} /> : <p>No prior-period data.</p>}</article><button type="button" onClick={() => { window.location.assign(`/api/v1/analytics/${scope === "creator" ? "creator/revenue-export.csv" : "platform/detail-export.csv"}${query}`); }}>Export CSV</button></>}</section>;
}

export function GroupAnalyticsDashboard() {
  const [groupId, setGroupId] = useState(""); const [report, setReport] = useState<Report | null>(null); const [error, setError] = useState(""); const [loading, setLoading] = useState(false); const [startsAt, setStartsAt] = useState(() => isoInput(30)); const [endsAt, setEndsAt] = useState(() => isoInput(0)); const [currency, setCurrency] = useState(""); const [source, setSource] = useState(""); const query = reportQuery(startsAt, endsAt, currency);
  async function load() { setLoading(true); setError(""); try { setReport(await api<Report>(`/analytics/groups/${groupId}/overview${query}`)); } catch (reason) { setError(reason instanceof ApiError && reason.status === 403 ? "You do not have permission to view this group analytics scope." : "Unable to load group analytics."); } finally { setLoading(false); } }
  const change = (name: string, value: string) => ({ startsAt: setStartsAt, endsAt: setEndsAt, currency: setCurrency, source: setSource }[name] as (value: string) => void)(value);
  return <section><label>Group ID<input value={groupId} onChange={(event) => setGroupId(event.target.value)} /></label><Controls {...{ startsAt, endsAt, currency, source, onChange: change }} /><button type="button" onClick={() => void load()} disabled={!groupId || loading}>{loading ? "Loading…" : "Load analytics"}</button>{report && !loading && <><DashboardReport report={report} source={source} title="Group KPIs" /><button type="button" onClick={() => { window.location.assign(`/api/v1/analytics/groups/${groupId}/revenue-export.csv${query}`); }}>Export CSV</button></>}{error && <p className="error" role="alert">{error}</p>}</section>;
}
