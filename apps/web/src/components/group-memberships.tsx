"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";

type Membership = { id: string; group_id: string; status: string; affiliation_public: boolean };

export function GroupMemberships() {
  const [items, setItems] = useState<Membership[]>([]); const [error, setError] = useState("");
  const refresh = () => api<Membership[]>("/groups/mine/memberships").then(setItems).catch((e: unknown) => setError(e instanceof ApiError ? e.message : "Unable to load memberships"));
  useEffect(() => { refresh(); }, []);
  async function act(path: string, method = "POST", body?: object) { try { await api(path, { method, body: body ? JSON.stringify(body) : undefined }); await refresh(); } catch (e) { setError(e instanceof ApiError ? e.message : "Group action failed"); } }
  return <section><h2>Group contracts</h2><ul>{items.map((item) => <li key={item.id}>Group {item.group_id}: {item.status} {item.status === "invited" && <><button onClick={() => act(`/groups/memberships/${item.id}/accept`)}>Accept</button><button onClick={() => act(`/groups/memberships/${item.id}/reject`)}>Reject</button></>} {item.status === "active" && <><button onClick={() => act(`/groups/memberships/${item.id}/leave`)}>Leave</button><label><input type="checkbox" checked={item.affiliation_public} onChange={(e) => act(`/groups/memberships/${item.id}/affiliation`, "PATCH", { visible: e.target.checked })} /> Show affiliation</label></>}</li>)}</ul>{error && <p className="error">{error}</p>}</section>;
}
