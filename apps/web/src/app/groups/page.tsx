"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "../../lib/api";

type Group = { id: string; name: string; slug: string; default_creator_basis_points: number };
type Creator = { id: string; creator_id: string; username: string | null; display_name: string | null; status: string; active_contract: { id: string; version: number; creator_basis_points: number; group_basis_points: number; status: string } | null };
type Dashboard = { currency: string; active_creators: number; pending_amount_minor: number; available_amount_minor: number };

export default function GroupsPage() {
  const [groups, setGroups] = useState<Group[]>([]); const [selected, setSelected] = useState(""); const [creators, setCreators] = useState<Creator[]>([]); const [dashboard, setDashboard] = useState<Dashboard | null>(null); const [error, setError] = useState("");
  useEffect(() => { api<Group[]>("/groups/mine/managed").then((rows) => { setGroups(rows); setSelected(rows[0]?.id ?? ""); }).catch((e: unknown) => setError(e instanceof ApiError ? e.message : "Unable to load managed groups")); }, []);
  useEffect(() => { if (!selected) return; Promise.all([api<Creator[]>(`/groups/${selected}/managed-creators`), api<Dashboard>(`/groups/${selected}/dashboard?currency=EUR`)]).then(([memberRows, dashboardRow]) => { setCreators(memberRows); setDashboard(dashboardRow); }).catch((e: unknown) => setError(e instanceof ApiError ? e.message : "Unable to load group dashboard")); }, [selected]);
  return <section className="card"><p className="eyebrow">GROUP MANAGER</p><h1>Managed creators</h1><label>Group<select value={selected} onChange={(e) => setSelected(e.target.value)}>{groups.map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}</select></label>{dashboard && <section><h2>Ledger dashboard</h2><p>Managed creators: {dashboard.active_creators}</p><p>Pending: {dashboard.pending_amount_minor} {dashboard.currency}</p><p>Available: {dashboard.available_amount_minor} {dashboard.currency}</p></section>}<ul>{creators.map((creator) => <li key={creator.id}><strong>{creator.display_name ?? creator.username ?? creator.creator_id}</strong> — {creator.status} {creator.active_contract && <>contract v{creator.active_contract.version}: creator {creator.active_contract.creator_basis_points / 100}% / group {creator.active_contract.group_basis_points / 100}% ({creator.active_contract.status})</>}</li>)}</ul>{error && <p className="error">{error}</p>}</section>;
}
