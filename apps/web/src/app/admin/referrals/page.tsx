"use client";

import { FormEvent, useEffect, useState } from "react";

import { api, ApiError } from "../../../lib/api";

type Affiliate = { id: string; public_id: string; name: string; status: string };
type Program = { id: string; public_id: string; status: string };
type Policy = { id: string; public_id: string; version: number };

export default function AdminReferralsPage() {
  const [affiliates, setAffiliates] = useState<Affiliate[]>([]);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const refresh = () => api<Affiliate[]>("/admin/affiliates")
    .then(setAffiliates)
    .catch((e: unknown) => setError(e instanceof ApiError ? e.message : "Unable to load affiliates"));
  useEffect(() => { void refresh(); }, []);

  async function submitAffiliate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await api<Affiliate>("/admin/affiliates", {
        method: "POST",
        body: JSON.stringify({ name: form.get("name"), owner_user_id: form.get("owner_user_id") || undefined }),
      });
      event.currentTarget.reset(); setMessage("Affiliate created."); await refresh();
    } catch (e) { setError(e instanceof ApiError ? e.message : "Affiliate could not be created"); }
  }

  async function setStatus(id: string, status: string) {
    try {
      await api<Affiliate>(`/admin/affiliates/${id}/status`, { method: "PUT", body: JSON.stringify({ status }) });
      setMessage("Affiliate status updated."); await refresh();
    } catch (e) { setError(e instanceof ApiError ? e.message : "Affiliate status could not be changed"); }
  }

  async function submitProgram(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const program = await api<Program>("/admin/referrals/programs", {
        method: "POST",
        body: JSON.stringify({
          actor_type: form.get("actor_type"), program_type: form.get("program_type"),
          owner_user_id: form.get("owner_user_id") || undefined,
          owner_creator_id: form.get("owner_creator_id") || undefined,
          affiliate_partner_id: form.get("affiliate_partner_id") || undefined,
          terms_reference: form.get("terms_reference") || undefined,
        }),
      });
      setMessage(`Program ${program.public_id} created.`); event.currentTarget.reset();
    } catch (e) { setError(e instanceof ApiError ? e.message : "Program could not be created"); }
  }

  async function submitPolicy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const programId = String(form.get("program_id"));
      const policy = await api<Policy>(`/admin/referrals/programs/${programId}/policies`, {
        method: "POST",
        body: JSON.stringify({
          basis_points: Number(form.get("basis_points")),
          eligible_revenue_types: String(form.get("revenue_types")).split(",").map((value) => value.trim()).filter(Boolean),
          attribution_window_days: Number(form.get("attribution_window_days")),
          subscription_reward_window_days: Number(form.get("subscription_reward_window_days")),
        }),
      });
      setMessage(`Policy ${policy.public_id} version ${policy.version} created.`); event.currentTarget.reset();
    } catch (e) { setError(e instanceof ApiError ? e.message : "Policy could not be created"); }
  }

  async function submitLink(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const programId = String(form.get("program_id"));
      await api(`/admin/referrals/programs/${programId}/links`, {
        method: "POST",
        body: JSON.stringify({ policy_id: form.get("policy_id"), code: form.get("code"), destination_path: form.get("destination_path"), source: form.get("source") || undefined }),
      });
      setMessage("Referral link created."); event.currentTarget.reset();
    } catch (e) { setError(e instanceof ApiError ? e.message : "Link could not be created"); }
  }

  return <section className="card">
    <p className="eyebrow">ADMIN</p><h1>Referral and affiliate management</h1>
    <p>All changes are server-authorized and audited. Policies version future rewards; they never recalculate historical allocations.</p>
    <h2>Affiliates</h2>
    <form onSubmit={submitAffiliate}><label>Name<input name="name" required /></label><label>Owner account ID<input name="owner_user_id" /></label><button>Create affiliate</button></form>
    <ul>{affiliates.map((affiliate) => <li key={affiliate.id}>{affiliate.name} ({affiliate.status}) <button onClick={() => void setStatus(affiliate.id, "paused")}>Pause</button><button onClick={() => void setStatus(affiliate.id, "active")}>Activate</button><button onClick={() => void setStatus(affiliate.id, "suspended")}>Suspend</button></li>)}</ul>
    <h2>Program</h2>
    <form onSubmit={submitProgram}><label>Actor type<select name="actor_type"><option value="user">User</option><option value="creator">Creator</option><option value="affiliate_partner">Affiliate</option></select></label><label>Program type<select name="program_type"><option value="user_user_referral">User to user</option><option value="creator_buyer_referral">Creator to buyer</option><option value="affiliate_referral">Affiliate</option><option value="creator_creator_referral">Creator to creator (paused)</option></select></label><label>Owner account ID<input name="owner_user_id" /></label><label>Owner creator ID<input name="owner_creator_id" /></label><label>Affiliate ID<input name="affiliate_partner_id" /></label><label>Terms reference<input name="terms_reference" /></label><button>Create program</button></form>
    <h2>Versioned policy</h2>
    <form onSubmit={submitPolicy}><label>Program ID<input name="program_id" required /></label><label>Commission basis points<input name="basis_points" type="number" min="0" max="10000" required /></label><label>Revenue types (comma-separated)<input name="revenue_types" defaultValue="ppv" required /></label><label>Attribution days<input name="attribution_window_days" type="number" min="1" defaultValue="30" required /></label><label>Subscription reward days<input name="subscription_reward_window_days" type="number" min="1" defaultValue="90" required /></label><button>Create policy version</button></form>
    <h2>Referral link</h2>
    <form onSubmit={submitLink}><label>Program ID<input name="program_id" required /></label><label>Policy ID<input name="policy_id" required /></label><label>Code<input name="code" required /></label><label>Internal destination<input name="destination_path" defaultValue="/" required /></label><label>Source<input name="source" /></label><button>Create link</button></form>
    {message && <p role="status">{message}</p>}{error && <p className="error">{error}</p>}
  </section>;
}
