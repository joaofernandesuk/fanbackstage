"use client";

import { useEffect, useState } from "react";

import { api, ApiError } from "../lib/api";

type Attachment = { id: string; message_id: string };
type Access = {
  id: string;
  media_type: string;
  locked: boolean;
  amount_minor: number | null;
  currency: string | null;
  preview_delivery_path: string | null;
  full_delivery_path: string | null;
};

const apiBase = process.env.NEXT_PUBLIC_FANBACKSTAGE_API_URL ?? "http://localhost:8000";

export function MessageAttachments({ conversationId }: { conversationId: string }) {
  const [items, setItems] = useState<Attachment[]>([]);
  const [access, setAccess] = useState<Record<string, Access>>({});
  const [error, setError] = useState("");

  async function refresh() {
    const attachments = await api<Attachment[]>(`/messages/conversations/${conversationId}/attachments`);
    setItems(attachments);
    const resolved = await Promise.all(attachments.map(async (item) => [item.id, await api<Access>(`/messages/attachments/${item.id}/access`)] as const));
    setAccess(Object.fromEntries(resolved));
  }
  useEffect(() => { void refresh().catch(() => setError("Unable to load message media")); }, [conversationId]);
  async function unlock(id: string) {
    try {
      const purchase = await api<{ payment_attempt_id: string }>(`/messages/attachments/${id}/unlock`, {
        method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() },
      });
      await api(`/payments/development/${purchase.payment_attempt_id}/complete`, { method: "POST" });
      await refresh();
    } catch (requestError) {
      setError(requestError instanceof ApiError ? requestError.message : "Unable to unlock media");
    }
  }
  return <section aria-label="Message attachments">{items.map((item) => { const value = access[item.id]; return value ? <article key={item.id}><p>{value.locked ? `Locked attachment · ${value.amount_minor} ${value.currency}` : "Unlocked attachment"}</p>{value.preview_delivery_path && <img alt="Locked attachment preview" src={`${apiBase}/api/v1${value.preview_delivery_path}`} />}{value.full_delivery_path && <a href={`${apiBase}/api/v1${value.full_delivery_path}`}>Open full media</a>}{value.locked && <button onClick={() => void unlock(item.id)}>Unlock attachment</button>}</article> : null; })}{error && <p className="error">{error}</p>}</section>;
}
