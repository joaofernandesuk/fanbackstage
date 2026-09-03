"use client";

import { FormEvent, useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";

type Tip = { id: string; label: string; amount_minor: number; currency: string; enabled: boolean; sort_order: number };
type Goal = { id: string; title: string; target_amount_minor: number; currency: string; active: boolean; progress_amount_minor: number };

export function LiveCommerceSettings() {
  const [tips, setTips] = useState<Tip[]>([]);
  const [goals, setGoals] = useState<Goal[]>([]);
  const [message, setMessage] = useState("");
  async function refresh() {
    const [nextTips, nextGoals] = await Promise.all([
      api<Tip[]>("/live/tip-menu"), api<Goal[]>("/live/goals"),
    ]);
    setTips(nextTips); setGoals(nextGoals);
  }
  useEffect(() => { void refresh(); }, []);
  async function saveTip(event: FormEvent<HTMLFormElement>, tip?: Tip) {
    event.preventDefault(); const form = event.currentTarget; const data = new FormData(form);
    try {
      await api(tip ? `/live/tip-menu/${tip.id}` : "/live/tip-menu", {
        method: tip ? "PUT" : "POST",
        body: JSON.stringify({ label: data.get("label"), amount_minor: Number(data.get("amount")), enabled: tip?.enabled ?? true, sort_order: Number(data.get("order")) }),
      });
      if (!tip) form.reset(); setMessage(tip ? "Tip item updated." : "Tip item saved."); await refresh();
    } catch (error) { setMessage(error instanceof ApiError ? error.message : "Tip item could not be saved"); }
  }
  async function toggleTip(tip: Tip) { await api(`/live/tip-menu/${tip.id}`, { method: "PUT", body: JSON.stringify({ label: tip.label, amount_minor: tip.amount_minor, enabled: !tip.enabled, sort_order: tip.sort_order }) }); await refresh(); }
  async function saveGoal(event: FormEvent<HTMLFormElement>, goal?: Goal) {
    event.preventDefault(); const form = event.currentTarget; const data = new FormData(form);
    try {
      await api(goal ? `/live/goals/${goal.id}` : "/live/goals", { method: goal ? "PUT" : "POST", body: JSON.stringify({ title: data.get("title"), target_amount_minor: Number(data.get("target")), ...(goal ? { active: goal.active } : {}) }) });
      if (!goal) form.reset(); setMessage(goal ? "Goal settings updated." : "Live goal created. Progress will come only from confirmed Live payments."); await refresh();
    } catch (error) { setMessage(error instanceof ApiError ? error.message : "Goal could not be saved"); }
  }
  async function toggleGoal(goal: Goal) { await api(`/live/goals/${goal.id}`, { method: "PUT", body: JSON.stringify({ title: goal.title, target_amount_minor: goal.target_amount_minor, active: !goal.active }) }); await refresh(); }
  async function resetGoal(goal: Goal) { await api(`/live/goals/${goal.id}/reset`, { method: "POST" }); setMessage("Goal reset from this moment; prior financial history remains immutable."); await refresh(); }
  return <section className="card" aria-label="Live creator commerce settings"><p className="eyebrow">LIVE COMMERCE</p><h2>Tip menu and goals</h2><p>Preview and configure what fans see. Currency follows your server-owned Live settings.</p>
    <form onSubmit={(event) => void saveTip(event)}><h3>Add tip menu item</h3><label>Label<input maxLength={100} name="label" required /></label><label>Price (minor units)<input min="1" name="amount" required type="number" /></label><label>Display order<input defaultValue="0" max="100" min="0" name="order" required type="number" /></label><button>Add tip item</button></form>
    {tips.map((tip) => <form aria-label={`Edit tip ${tip.label}`} key={tip.id} onSubmit={(event) => void saveTip(event, tip)}><label>Label<input defaultValue={tip.label} maxLength={100} name="label" required /></label><label>Price (minor units)<input defaultValue={tip.amount_minor} min="1" name="amount" required type="number" /></label><label>Display order<input defaultValue={tip.sort_order} max="100" min="0" name="order" required type="number" /></label><span>{tip.currency} · {tip.enabled ? "active" : "inactive"}</span><button>Save tip changes</button><button onClick={() => void toggleTip(tip)} type="button">{tip.enabled ? "Deactivate tip" : "Activate tip"}</button></form>)}
    <form onSubmit={(event) => void saveGoal(event)}><h3>Create goal</h3><label>Title<input maxLength={140} name="title" required /></label><label>Target (minor units)<input min="1" name="target" required type="number" /></label><button>Create goal</button></form>
    {goals.map((goal) => <form aria-label={`Edit goal ${goal.title}`} key={goal.id} onSubmit={(event) => void saveGoal(event, goal)}><label>Title<input defaultValue={goal.title} maxLength={140} name="title" required /></label><label>Target (minor units)<input defaultValue={goal.target_amount_minor} min="1" name="target" required type="number" /></label><span>{goal.currency} · {goal.active ? "active" : "inactive"}</span><button>Save goal changes</button><button onClick={() => void toggleGoal(goal)} type="button">{goal.active ? "Deactivate goal" : "Activate goal"}</button><button onClick={() => void resetGoal(goal)} type="button">Reset from now</button></form>)}
    {message && <p role="status">{message}</p>}
  </section>;
}
