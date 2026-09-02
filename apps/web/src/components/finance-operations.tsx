"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { ApiError, api, type CurrentUser } from "../lib/api";
import styles from "./operations-workspace.module.css";

type Payment = { id: string; provider: string; provider_reference: string; amount_minor: number; currency: string; status: string; created_at: string; buyer: string; creator: string | null; source: { domain: string; label: string; status: string } | null; refund_requirement: { status: string; reason: string } | null };
type Detail = { payment: Payment; buyer: { email: string }; creator: { username: string; display_name: string } | null; source: { domain: string; label: string; status: string } | null; refund_requirement: { status: string; reason: string; amount_minor: number } | null; ledger: { id: string; type: string; reference: string; effective_at: string; entries: { direction: string; amount_minor: number; currency: string; account: string }[] }[]; provider_events: { id: string; type: string; external_event_id: string; created_at: string }[]; audit: { id: string; type: string; created_at: string }[]; can_request_refund: boolean };

export function formatMoney(amount: number, currency: string) {
  return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(amount / 100);
}

export function FinanceOperations() {
  const [items, setItems] = useState<Payment[]>([]); const [total, setTotal] = useState(0); const [page, setPage] = useState(1);
  const [search, setSearch] = useState(""); const [status, setStatus] = useState(""); const [source, setSource] = useState(""); const [provider, setProvider] = useState("");
  const [creator, setCreator] = useState(""); const [currency, setCurrency] = useState(""); const [refundState, setRefundState] = useState(""); const [startsAt, setStartsAt] = useState(""); const [endsAt, setEndsAt] = useState("");
  const [exceptions, setExceptions] = useState(false);
  const [canMutate, setCanMutate] = useState(false);
  const [selected, setSelected] = useState<Detail | null>(null); const [reason, setReason] = useState(""); const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState(""); const [notice, setNotice] = useState(""); const pageSize = 25;
  const load = useCallback(async () => {
    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (search) params.set("search", search); if (creator) params.set("creator", creator); if (status) params.set("status", status); if (source) params.set("source_domain", source); if (provider) params.set("provider", provider); if (currency) params.set("currency", currency); if (refundState) params.set("refund_state", refundState); if (startsAt) params.set("starts_at", new Date(`${startsAt}T00:00:00Z`).toISOString()); if (endsAt) params.set("ends_at", new Date(`${endsAt}T23:59:59Z`).toISOString()); if (exceptions) params.set("exceptions", "true");
    try { const result = await api<{ items: Payment[]; total: number }>(`/admin/finance/operations?${params}`); setItems(result.items); setTotal(result.total); setError(""); }
    catch (caught) { setError(caught instanceof ApiError ? caught.message : "Unable to load finance operations."); }
  }, [creator, currency, endsAt, exceptions, page, provider, refundState, search, source, startsAt, status]);
  useEffect(() => { if (new URLSearchParams(window.location.search).get("exceptions") === "true") setExceptions(true); }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { void api<CurrentUser>("/me").then((user) => setCanMutate(user.roles.includes("super_admin"))); }, []);
  const open = async (id: string) => { try { setSelected(await api<Detail>(`/admin/finance/operations/${id}`)); setError(""); } catch (caught) { setError(caught instanceof ApiError ? caught.message : "Unable to open payment."); } };
  const filter = (event: FormEvent) => { event.preventDefault(); setPage(1); void load(); };
  const refund = async () => { if (!selected) return; try { await api(`/admin/finance/operations/${selected.payment.id}/refund`, { method: "POST", body: JSON.stringify({ reason, confirmed }) }); setNotice("Refund command queued through the staging provider."); setReason(""); setConfirmed(false); await open(selected.payment.id); await load(); } catch (caught) { setError(caught instanceof ApiError ? caught.message : "Unable to request refund."); } };
  const reconcile = async () => { if (!window.confirm("Run the bounded reconciliation command for up to 25 confirmed payments?")) return; try { const result = await api<{ reconciled: number }>("/admin/finance/reconciliation", { method: "POST", body: JSON.stringify({ confirmed: true, limit: 25 }) }); setNotice(`Reconciliation completed for ${result.reconciled} record(s).`); await load(); } catch (caught) { setError(caught instanceof ApiError ? caught.message : "Reconciliation is not permitted."); } };
  return <main className={styles.page}>
    <header className={styles.hero}><p className="eyebrow">FINANCE OPERATIONS</p><h1>Payment lifecycle</h1><p>Search bounded provider and ledger-backed history. Original transactions remain immutable.</p></header>
    <section className={styles.panel}><form className={styles.filters} onSubmit={filter}>
      <label>Account or provider reference<input onChange={(e) => setSearch(e.target.value)} value={search} /></label>
      <label>Creator<input onChange={(e) => setCreator(e.target.value)} value={creator} /></label>
      <label>Status<select onChange={(e) => setStatus(e.target.value)} value={status}><option value="">All</option>{["pending", "succeeded", "failed", "refunded", "disputed", "chargeback"].map((v) => <option key={v}>{v}</option>)}</select></label>
      <label>Source<select onChange={(e) => setSource(e.target.value)} value={source}><option value="">All</option>{["ppv", "subscription", "marketplace", "message_unlock", "paid_message", "private_live", "live_commerce", "featuring"].map((v) => <option key={v}>{v}</option>)}</select></label>
      <label>Provider<input onChange={(e) => setProvider(e.target.value)} value={provider} /></label>
      <label>Currency<input maxLength={3} onChange={(e) => setCurrency(e.target.value.toUpperCase())} value={currency} /></label>
      <label>Refund state<select onChange={(e) => setRefundState(e.target.value)} value={refundState}><option value="">All</option>{["required", "completed"].map((v) => <option key={v}>{v}</option>)}</select></label>
      <label>From<input onChange={(e) => setStartsAt(e.target.value)} type="date" value={startsAt} /></label>
      <label>To<input onChange={(e) => setEndsAt(e.target.value)} type="date" value={endsAt} /></label>
      <label className={styles.confirm}><input checked={exceptions} onChange={(e) => setExceptions(e.target.checked)} type="checkbox" /> Exceptions only</label><button>Apply filters</button>
    </form>
    <div className={styles.tabs}><button className={styles.secondary} onClick={() => { setStatus("pending"); setPage(1); }} type="button">Pending</button><button className={styles.secondary} onClick={() => { setStatus("disputed"); setPage(1); }} type="button">Disputes</button>{canMutate && <button className={styles.secondary} onClick={() => void reconcile()} type="button">Run safe reconciliation</button>}</div>
    {error && <p className={styles.error} role="alert">{error}</p>}{notice && <p className={styles.notice} role="status">{notice}</p>}
    <div className={styles.split}><div className={styles.tableWrap}><table className={styles.table}><thead><tr><th>Payment</th><th>Source</th><th>Amount</th><th>Status</th><th>Date</th></tr></thead><tbody>{items.map((item) => <tr key={item.id}><td><button className={styles.rowButton} onClick={() => void open(item.id)}><strong>{item.buyer}</strong><span>{item.provider_reference}</span></button></td><td>{item.source?.label ?? "Unlinked"}</td><td className={styles.money}>{formatMoney(item.amount_minor, item.currency)}</td><td><span className={styles.status}>{item.status}</span></td><td>{new Date(item.created_at).toLocaleString()}</td></tr>)}</tbody></table>{!items.length && <p className={styles.empty}>No payments match these filters.</p>}<div className={styles.pagination}><button disabled={page === 1} onClick={() => setPage((v) => v - 1)}>Previous</button><span>Page {page}</span><button disabled={page * pageSize >= total} onClick={() => setPage((v) => v + 1)}>Next</button></div></div>
    <aside className={`${styles.panel} ${styles.detail}`}>{selected ? <><div className={styles.detailHeader}><div><p className="eyebrow">PAYMENT DETAIL</p><h2>{selected.source?.label ?? "Payment"}</h2></div><span className={styles.status}>{selected.payment.status}</span></div><dl className={styles.facts}><div><dt>Provider reference</dt><dd>{selected.payment.provider_reference}</dd></div><div><dt>Provider</dt><dd>{selected.payment.provider}</dd></div><div><dt>Buyer</dt><dd>{selected.buyer.email}</dd></div><div><dt>Creator</dt><dd>{selected.creator ? (selected.creator.display_name || selected.creator.username) : "Not applicable"}</dd></div><div><dt>Amount</dt><dd>{formatMoney(selected.payment.amount_minor, selected.payment.currency)}</dd></div><div><dt>Refund review</dt><dd>{selected.refund_requirement ? `${selected.refund_requirement.status}: ${selected.refund_requirement.reason}` : "None"}</dd></div></dl><h3>Ledger lifecycle</h3><ul className={styles.timeline}>{selected.ledger.map((tx) => <li key={tx.id}><strong>{tx.type}</strong> · {tx.entries.map((entry) => `${entry.direction} ${formatMoney(entry.amount_minor, entry.currency)} ${entry.account}`).join("; ")}<small>{new Date(tx.effective_at).toLocaleString()}</small></li>)}</ul><h3>Provider events</h3><ul className={styles.timeline}>{selected.provider_events.map((event) => <li key={event.id}>{event.type}<small>{event.external_event_id} · {new Date(event.created_at).toLocaleString()}</small></li>)}</ul><h3>Audit history</h3><ul className={styles.timeline}>{selected.audit.map((event) => <li key={event.id}>{event.type}<small>{new Date(event.created_at).toLocaleString()}</small></li>)}</ul>{canMutate && selected.can_request_refund && <div className={styles.decision}><h3>Request full refund</h3><p>The staging provider callback will apply the existing reversal rules. Partial refunds are not supported.</p><label>Reason<textarea onChange={(e) => setReason(e.target.value)} value={reason} /></label><label className={styles.confirm}><input checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} type="checkbox" /> I confirm this refund for {formatMoney(selected.payment.amount_minor, selected.payment.currency)}.</label><button disabled={!confirmed || reason.trim().length < 8} onClick={() => void refund()}>Queue refund</button></div>}</> : <p className={styles.empty}>Select a payment to inspect its safe operational history.</p>}</aside></div>
    </section>
  </main>;
}
