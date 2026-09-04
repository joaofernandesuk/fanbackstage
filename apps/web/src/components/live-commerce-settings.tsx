"use client";

import Image from "next/image";
import { FormEvent, useEffect, useState } from "react";

import { api, ApiError } from "../lib/api";
import { formatMoney } from "../lib/public-api";

type Tip = { id: string; label: string; icon: string; amount_minor: number; currency: string };
type Gift = { id: string; name: string; icon: string; amount_minor: number; currency: string };
type Goal = { id: string; title: string; target_amount_minor: number; currency: string; active: boolean; progress_amount_minor: number };
type LiveSettings = { snapshots_enabled: boolean; snapshot_price_minor: number; currency: string };

export function LiveCommerceSettings() {
  const [tips, setTips] = useState<Tip[]>([]);
  const [gifts, setGifts] = useState<Gift[]>([]);
  const [goals, setGoals] = useState<Goal[]>([]);
  const [message, setMessage] = useState("");
  const [settings, setSettings] = useState<LiveSettings | null>(null);
  const [panel, setPanel] = useState<"catalogue" | "snapshots" | "goals">("catalogue");

  async function refresh() {
    const [nextTips, nextGifts, nextGoals, nextSettings] = await Promise.all([
      api<Tip[]>("/live/tip-menu"),
      api<Gift[]>("/live/gifts"),
      api<Goal[]>("/live/goals"),
      api<LiveSettings>("/live/settings"),
    ]);
    setTips(nextTips);
    setGifts(nextGifts);
    setGoals(nextGoals);
    setSettings(nextSettings);
  }

  useEffect(() => { void refresh(); }, []);

  async function saveGoal(event: FormEvent<HTMLFormElement>, goal?: Goal) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      await api(goal ? `/live/goals/${goal.id}` : "/live/goals", {
        method: goal ? "PUT" : "POST",
        body: JSON.stringify({
          title: data.get("title"),
          target_amount_minor: Math.round(Number(data.get("target")) * 100),
          ...(goal ? { active: goal.active } : {}),
        }),
      });
      if (!goal) form.reset();
      setMessage(goal ? "Goal settings updated." : "Live goal created. Progress comes only from confirmed Live payments.");
      await refresh();
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "Goal could not be saved");
    }
  }

  async function toggleGoal(goal: Goal) {
    await api(`/live/goals/${goal.id}`, {
      method: "PUT",
      body: JSON.stringify({ title: goal.title, target_amount_minor: goal.target_amount_minor, active: !goal.active }),
    });
    await refresh();
  }

  async function resetGoal(goal: Goal) {
    await api(`/live/goals/${goal.id}/reset`, { method: "POST" });
    setMessage("Goal reset from this moment; prior financial history remains immutable.");
    await refresh();
  }

  async function saveSnapshotPricing(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    try {
      const updated = await api<LiveSettings>("/live/settings", {
        method: "PATCH",
        body: JSON.stringify({
          snapshots_enabled: values.get("snapshots-enabled") === "on",
          snapshot_price_minor: Math.round(Number(values.get("snapshot-price")) * 100),
        }),
      });
      setSettings(updated);
      setMessage("Paid snapshot settings saved. Existing financial history is unchanged.");
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "Snapshot pricing could not be saved");
    }
  }

  return (
    <section aria-label="Live creator commerce settings" className="card">
      <p className="eyebrow">LIVE COMMERCE</p>
      <h2>Shared tips and your goals</h2>
      <nav aria-label="Live commerce settings" className="live-settings-tabs">
        <button aria-pressed={panel === "catalogue"} onClick={() => setPanel("catalogue")} type="button">Tip & gift catalogue</button>
        <button aria-pressed={panel === "snapshots"} onClick={() => setPanel("snapshots")} type="button">Paid snapshots</button>
        <button aria-pressed={panel === "goals"} onClick={() => setPanel("goals")} type="button">Live goals</button>
      </nav>
      {panel === "catalogue" && <>
        <p>Tips and gifts are curated by FanBackstage and available to every creator. Prices and artwork cannot be changed per model.</p>
        <div aria-label="Platform tip catalogue" className="commerce-catalogue-preview">
        {tips.map((tip) => (
          <article key={tip.id}>
            <Image alt="" height={48} src={tip.icon} width={48} />
            <span><strong>{tip.label}</strong><small>{formatMoney(tip.amount_minor, tip.currency)}</small></span>
          </article>
        ))}
        </div>
        <h3>Shared gifts</h3>
        <div aria-label="Platform gift catalogue" className="commerce-catalogue-preview">
        {gifts.map((gift) => (
          <article key={gift.id}>
            <Image alt="" height={48} src={gift.icon} width={48} />
            <span><strong>{gift.name}</strong><small>{formatMoney(gift.amount_minor, gift.currency)}</small></span>
          </article>
        ))}
        </div>
      </>}
      {panel === "snapshots" && settings && <form aria-label="Paid snapshot settings" onSubmit={saveSnapshotPricing}>
        <h3>Paid snapshots</h3>
        <p>Fans can capture the current Live frame only after a confirmed payment. FanBackstage does not store the image.</p>
        <label><input defaultChecked={settings.snapshots_enabled} name="snapshots-enabled" type="checkbox" /> Allow paid snapshots</label>
        <label>Snapshot price ({settings.currency})<input defaultValue={(settings.snapshot_price_minor / 100).toFixed(2)} min="0.01" name="snapshot-price" required step="0.01" type="number" /></label>
        <button>Save snapshot settings</button>
      </form>}
      {panel === "goals" && <><form onSubmit={(event) => void saveGoal(event)}>
        <h3>Create goal</h3>
        <label>Title<input maxLength={140} name="title" required /></label>
        <label>Target (EUR)<input min="0.01" name="target" required step="0.01" type="number" /></label>
        <button>Create goal</button>
      </form>
      {goals.map((goal) => (
        <form aria-label={`Edit goal ${goal.title}`} key={goal.id} onSubmit={(event) => void saveGoal(event, goal)}>
          <label>Title<input defaultValue={goal.title} maxLength={140} name="title" required /></label>
          <label>Target ({goal.currency})<input defaultValue={(goal.target_amount_minor / 100).toFixed(2)} min="0.01" name="target" required step="0.01" type="number" /></label>
          <span>{goal.currency} · {goal.active ? "active" : "inactive"}</span>
          <button>Save goal changes</button>
          <button onClick={() => void toggleGoal(goal)} type="button">{goal.active ? "Deactivate goal" : "Activate goal"}</button>
          <button onClick={() => void resetGoal(goal)} type="button">Reset from now</button>
        </form>
      ))}</>}
      {message && <p role="status">{message}</p>}
    </section>
  );
}
