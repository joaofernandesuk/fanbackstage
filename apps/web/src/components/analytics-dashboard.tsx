"use client";

import { useEffect, useMemo, useState } from "react";

import { api, ApiError } from "../lib/api";
import styles from "./analytics-dashboard.module.css";

type Currency = Record<string, string | number | null> & { currency: string };
type Overview = { metric_definition_version?: string; users?: number; paid_users?: number; new_creators?: number; approved_creators?: number; currencies: Currency[] };
type Growth = { user_funnel: Record<string, number>; creator_funnel: Record<string, number> };
type Cohorts = { retention: Record<string, { rate: number | null; returned: number; denominator: number }> };

const currencyFormatter = (value: unknown, currency: string) => new Intl.NumberFormat(undefined, { style: "currency", currency, maximumFractionDigits: 2 }).format((typeof value === "number" ? value : 0) / 100);
const numeric = (value: number | undefined) => new Intl.NumberFormat().format(value ?? 0);
const label = (value: string) => value.replaceAll("_", " ");

function isoInput(daysAgo: number) { return new Date(Date.now() - daysAgo * 86_400_000).toISOString().slice(0, 16); }
function queryFor(startsAt: string, endsAt: string, currency: string) { const query = new URLSearchParams(); if (startsAt) query.set("starts_at", new Date(startsAt).toISOString()); if (endsAt) query.set("ends_at", new Date(endsAt).toISOString()); if (currency) query.set("currency", currency); return query.toString() ? `?${query}` : ""; }

function Filters({ startsAt, endsAt, currency, onChange }: { startsAt: string; endsAt: string; currency: string; onChange: (field: "startsAt" | "endsAt" | "currency", value: string) => void }) {
  return <fieldset className={styles.filters}><legend>Report period</legend><label>From<input type="datetime-local" value={startsAt} onChange={(event) => onChange("startsAt", event.target.value)} /></label><label>To<input type="datetime-local" value={endsAt} onChange={(event) => onChange("endsAt", event.target.value)} /></label><label>Currency<select aria-label="Currency" value={currency} onChange={(event) => onChange("currency", event.target.value)}><option value="">All currencies</option><option value="EUR">EUR</option><option value="GBP">GBP</option><option value="USD">USD</option></select></label></fieldset>;
}

function Funnel({ title, values }: { title: string; values: Record<string, number> }) {
  const maximum = Math.max(...Object.values(values), 1);
  return <section className={styles.panel}><div className={styles.panelHeader}><h2>{title}</h2><span>Aggregate funnel</span></div><div className={styles.funnel}>{Object.entries(values).map(([key, value]) => <div className={styles.funnelRow} key={key}><div><strong>{label(key)}</strong><span>{numeric(value)}</span></div><i style={{ width: `${Math.max(5, value / maximum * 100)}%` }} /></div>)}</div></section>;
}

function RevenueComposition({ currencies }: { currencies: Currency[] }) {
  if (!currencies.length) return <section className={styles.panel}><h2>Revenue composition</h2><p>No ledger activity is available for this period.</p></section>;
  const fields = ["platform_retained_net_minor", "creator_distributable_minor", "group_distributable_minor", "refunds_minor", "chargebacks_minor"] as const;
  return <section className={styles.panel}><div className={styles.panelHeader}><h2>Revenue composition</h2><span>Ledger-derived · currency-separated</span></div><div className={styles.composition}>{currencies.map((row) => { const maximum = Math.max(...fields.map((field) => Math.abs(Number(row[field]) || 0)), 1); return <article key={row.currency}><header><strong>{row.currency}</strong><b>{currencyFormatter(row.gmv_minor, row.currency)}</b><span>gross merchandise value</span></header>{fields.map((field) => <div className={styles.compositionRow} key={field}><span>{label(field.replace("_minor", ""))}</span><i style={{ width: `${Math.abs(Number(row[field]) || 0) / maximum * 100}%` }} /><b>{currencyFormatter(row[field], row.currency)}</b></div>)}</article>; })}</div></section>;
}

function Retention({ retention }: { retention: Cohorts["retention"] }) {
  return <section className={styles.panel}><div className={styles.panelHeader}><h2>Retention</h2><span>Previous period → current period</span></div><div className={styles.retention}>{Object.entries(retention).map(([key, value]) => <div key={key}><strong>{label(key)}</strong><b>{value.rate === null ? "—" : `${Math.round(value.rate * 100)}%`}</b><p>{numeric(value.returned)} of {numeric(value.denominator)} returned</p></div>)}</div></section>;
}

export function AnalyticsDashboard({ scope }: { scope: "creator" | "platform" }) {
  const [report, setReport] = useState<Overview | null>(null); const [previous, setPrevious] = useState<Overview | null>(null); const [growth, setGrowth] = useState<Growth | null>(null); const [cohorts, setCohorts] = useState<Cohorts | null>(null); const [error, setError] = useState(""); const [loading, setLoading] = useState(true);
  const [startsAt, setStartsAt] = useState(() => isoInput(30)); const [endsAt, setEndsAt] = useState(() => isoInput(0)); const [currency, setCurrency] = useState(""); const query = queryFor(startsAt, endsAt, currency);
  useEffect(() => { let active = true; setLoading(true); setError(""); const start = new Date(startsAt); const end = new Date(endsAt); const previousStart = new Date(start.getTime() - (end.getTime() - start.getTime())); const requests: Promise<unknown>[] = [api<Overview>(`/analytics/${scope}/overview${query}`), api<Overview>(`/analytics/${scope}/overview${queryFor(previousStart.toISOString().slice(0, 16), startsAt, currency)}`)]; if (scope === "platform") requests.push(api<Growth>(`/analytics/platform/growth${query}`), api<Cohorts>(`/analytics/platform/cohorts${query}`)); void Promise.all(requests).then(([current, prior, nextGrowth, nextCohorts]) => { if (active) { setReport(current as Overview); setPrevious(prior as Overview); setGrowth((nextGrowth as Growth | undefined) ?? null); setCohorts((nextCohorts as Cohorts | undefined) ?? null); } }).catch((caught: unknown) => { if (active) setError(caught instanceof ApiError && caught.status === 403 ? "You do not have permission to view this analytics scope." : "Unable to load analytics."); }).finally(() => { if (active) setLoading(false); }); return () => { active = false; }; }, [scope, query, startsAt, endsAt, currency]);
  const kpis = useMemo(() => report ? [{ label: "Total users", value: report.users, prior: previous?.users }, { label: "Paid users", value: report.paid_users, prior: previous?.paid_users }, { label: "New creators", value: report.new_creators, prior: previous?.new_creators }, { label: "Approved creators", value: report.approved_creators, prior: previous?.approved_creators }] : [], [report, previous]);
  const change = (field: "startsAt" | "endsAt" | "currency", value: string) => ({ startsAt: setStartsAt, endsAt: setEndsAt, currency: setCurrency }[field])(value);
  return <section className={styles.dashboard}><Filters {...{ startsAt, endsAt, currency, onChange: change }} />{loading && <p className={styles.loading} role="status">Loading analytics…</p>}{error && <p className={styles.error} role="alert">{error}</p>}{!loading && !error && report && <><section aria-label="Key performance indicators" className={styles.kpis}>{kpis.map((kpi) => <article key={kpi.label}><span>{kpi.label}</span><strong>{numeric(kpi.value)}</strong><small>{`${(kpi.value ?? 0) - (kpi.prior ?? 0) >= 0 ? "+" : ""}${numeric((kpi.value ?? 0) - (kpi.prior ?? 0))} vs prior period`}</small></article>)}</section><RevenueComposition currencies={report.currencies} />{growth ? <section className={styles.split}><Funnel title="Audience funnel" values={growth.user_funnel} /><Funnel title="Creator funnel" values={growth.creator_funnel} /></section> : null}{cohorts ? <Retention retention={cohorts.retention} /> : null}<section className={styles.footer}><p>Definition: {report.metric_definition_version ?? "phase14.v1"}. Financial values are ledger-derived and remain separated by currency.</p><button type="button" onClick={() => { window.location.assign(`/api/v1/analytics/${scope === "creator" ? "creator/revenue-export.csv" : "platform/detail-export.csv"}${query}`); }}>Export CSV</button></section></>}</section>;
}

export function GroupAnalyticsDashboard() { return <section className="card"><h2>Group analytics</h2><p>Open a group workspace to review its authorised group analytics.</p></section>; }
